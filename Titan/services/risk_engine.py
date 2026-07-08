import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, get_settings
from Titan.core.state import state
from Titan.core.logger import risk_logger
from Titan.storage.db import get_db_connection

class RiskEngine:
    def __init__(self, symbol: str = PRIMARY_SYMBOL):
        self.symbol = symbol
        self.logger = risk_logger

    def check_drawdown_limit(self) -> bool:
        """Checks if the account drawdown has breached the daily threshold."""
        settings = get_settings()
        daily_loss_limit = float(settings.get("max_daily_loss", 3.0)) # % of equity
        
        # Calculate daily net PnL from closed trades today
        today_start_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(pnl) as today_pnl FROM trades WHERE status='CLOSED' AND close_time >= ?",
                (today_start_date + " 00:00:00",)
            )
            row = cursor.fetchone()
            today_closed_pnl = float(row["today_pnl"]) if row and row["today_pnl"] is not None else 0.0
        except Exception as e:
            self.logger.error(f"Error reading daily pnl from database: {e}")
            today_closed_pnl = 0.0
        finally:
            conn.close()

        state.lock.acquire()
        equity = state.equity
        balance = state.balance
        floating_profit = state.profit
        state.lock.release()

        if balance <= 0:
            return False
            
        today_net_pnl = today_closed_pnl + floating_profit
        max_allowed_loss_money = balance * (daily_loss_limit / 100.0)
        
        # Breached if net loss today exceeds allowed loss money
        if today_net_pnl < -max_allowed_loss_money:
            self.logger.warning(
                f"[Risk Alert] Daily loss threshold breached! "
                f"Today Net PnL: {today_net_pnl:.2f} | Max Loss Allowed: -{max_allowed_loss_money:.2f} ({daily_loss_limit}%)"
            )
            return True
            
        # 2b. Weekly Loss Limit check
        weekly_loss_limit = float(settings.get("max_weekly_loss", 8.0)) # % of equity
        week_start_date = (datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())).strftime('%Y-%m-%d')
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(pnl) as week_pnl FROM trades WHERE status='CLOSED' AND close_time >= ?",
                (week_start_date + " 00:00:00",)
            )
            row = cursor.fetchone()
            week_closed_pnl = float(row["week_pnl"]) if row and row["week_pnl"] is not None else 0.0
        except Exception as e:
            self.logger.error(f"Error reading weekly pnl from database: {e}")
            week_closed_pnl = 0.0
        finally:
            conn.close()
            
        week_net_pnl = week_closed_pnl + floating_profit
        max_allowed_week_loss_money = balance * (weekly_loss_limit / 100.0)
        
        if week_net_pnl < -max_allowed_week_loss_money:
            self.logger.warning(
                f"[Risk Alert] Weekly loss threshold breached! "
                f"Week Net PnL: {week_net_pnl:.2f} | Max Weekly Loss Allowed: -{max_allowed_week_loss_money:.2f} ({weekly_loss_limit}%)"
            )
            return True
            
        return False

    def qualify_new_order(self, action: str, current_tick: Dict[str, Any]) -> Dict[str, Any]:
        """Verifies account health, spread, sessions, position limits, and computes dynamic lot sizes."""
        settings = get_settings()
        
        # 1. Emergency Halt or Auto Trade flag check
        state.lock.acquire()
        halted = state.emergency_halt
        auto_trade = state.auto_trade
        state.lock.release()
        
        if halted:
            return {"qualified": False, "reason": "System is emergency halted."}
        if not auto_trade:
            return {"qualified": False, "reason": "Auto trade execution is disabled."}

        # 2. Daily Loss Limit qualification check
        if self.check_drawdown_limit():
            return {"qualified": False, "reason": "REJECTED: Daily drawdown threshold reached."}

        # 3. Spread qualification check
        spread_limit_pts = int(settings.get("spread_limit", 300))
        spread_pts = current_tick["spread"]
        if spread_pts > spread_limit_pts:
            return {"qualified": False, "reason": f"Wide spread ({spread_pts} pts) > Limit ({spread_limit_pts} pts)"}

        # 4. Position limits verification
        max_positions = int(settings.get("max_concurrent_positions", 2))
        
        state.lock.acquire()
        current_positions = list(state.open_positions)
        acct_equity = state.equity
        state.lock.release()

        if len(current_positions) >= max_positions:
            return {"qualified": False, "reason": f"Maximum concurrent positions reached ({max_positions})"}

        # 5. Prevent duplicate direction order
        for pos in current_positions:
            if pos["symbol"] == self.symbol and pos["type"] == action:
                return {"qualified": False, "reason": f"Position already open for {self.symbol} ({action})"}

        # Scale-in requirement (Rule 14): only scale in if existing positions are in profit
        for pos in current_positions:
            if pos["symbol"] == self.symbol:
                floating_pnl = pos.get("profit", 0.0)
                if floating_pnl < 0.0:
                    return {"qualified": False, "reason": "REJECTED: Scale-in blocked, first position in loss."}

        # 6. Sizing calculations
        sym_info = mt5.symbol_info(self.symbol)
        if sym_info is None:
            return {"qualified": False, "reason": "Failed to fetch symbol specs from MT5."}

        # Sizing ATR multiplier
        atr_mult = float(settings.get("atr_multiplier", 1.5))
        tp_mult = float(settings.get("tp_multiplier", 1.5))

        state.lock.acquire()
        m1_candles = list(state.candles)
        state.lock.release()

        from Titan.market.intelligence.utils import calculate_atr
        closes = [c["close"] for c in m1_candles]
        highs = [c["high"] for c in m1_candles]
        lows = [c["low"] for c in m1_candles]
        m1_atr = calculate_atr(highs, lows, closes, 14) if len(closes) >= 15 else 1.0

        # Define current price
        curr_price = current_tick["ask"] if action == "BUY" else current_tick["bid"]

        # Calculate dynamic stop loss using ATR and recent highs/lows (swings) - Rule 12
        recent_candles = m1_candles[-10:] if len(m1_candles) >= 10 else m1_candles
        recent_highs = [c["high"] for c in recent_candles]
        recent_lows = [c["low"] for c in recent_candles]
        recent_swing_high = max(recent_highs) if recent_highs else curr_price
        recent_swing_low = min(recent_lows) if recent_lows else curr_price
        
        spread_val = spread_pts * sym_info.point
        if action == "BUY":
            swing_sl = recent_swing_low - (m1_atr * 0.5) - spread_val
            val_sl = max(curr_price - 6.0, min(curr_price - 1.5, swing_sl))
        else:
            swing_sh = recent_swing_high + (m1_atr * 0.5) + spread_val
            val_sl = min(curr_price + 6.0, max(curr_price + 1.5, swing_sh))
            
        sl_points = max(150, min(650, int(abs(curr_price - val_sl) / sym_info.point)))
        
        # Calculate Win/Loss Streak from database for adaptive performance weighting
        conn = get_db_connection()
        streak = 0
        streak_type = "NEUTRAL"
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT pnl FROM trades WHERE status='CLOSED' ORDER BY close_time DESC LIMIT 5")
            rows = cursor.fetchall()
            if rows:
                first_pnl = float(rows[0]["pnl"])
                if first_pnl > 0:
                    streak_type = "WIN"
                    streak = 1
                    for r in rows[1:]:
                        if float(r["pnl"]) > 0:
                            streak += 1
                        else:
                            break
                elif first_pnl < 0:
                    streak_type = "LOSS"
                    streak = 1
                    for r in rows[1:]:
                        if float(r["pnl"]) < 0:
                            streak += 1
                        else:
                            break
        except Exception as e:
            self.logger.error(f"Error checking streak stats for risk sizing: {e}")
        finally:
            conn.close()

        # Calculate dynamic lot sizing with adaptive buffers
        risk_pct = float(settings.get("risk_pct", 1.0))
        adjusted_risk_pct = risk_pct
        
        # Rule 5: Recovery Check
        in_recovery_mode = False
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            # Fetch the last 8 closed trades
            cursor.execute("SELECT pnl FROM trades WHERE status='CLOSED' ORDER BY close_time DESC LIMIT 8")
            closed_rows = [float(r["pnl"]) for r in cursor.fetchall()]
            
            # Check if we recently hit a losing streak of 3 or more losses
            recent_losses = 0
            for p in reversed(closed_rows):
                if p < 0:
                    recent_losses += 1
                else:
                    recent_losses = 0
                    
            if recent_losses >= 3:
                # We hit a losing streak!
                in_recovery_mode = True
                
            # If we are in recovery mode, check if we recovered:
            # Did we have a recovery of at least 3 consecutive wins, or is the net P&L of the last 4 trades positive?
            if in_recovery_mode and len(closed_rows) >= 3:
                last_3_wins = all(p > 0 for p in closed_rows[:3])
                net_pnl_recent = sum(closed_rows[:4])
                if last_3_wins or net_pnl_recent > 0:
                    in_recovery_mode = False # Recovered!
        except Exception as e:
            self.logger.error(f"Error checking recovery mode: {e}")
        finally:
            conn.close()

        # Apply streak modifiers or recovery lock
        if in_recovery_mode:
            adjusted_risk_pct = risk_pct * 0.40 # Reduce risk by 60% locked
            self.logger.info(f"[Adaptive Risk] Locked in Recovery Mode. Sizing scaled down to 40% of nominal.")
        elif streak_type == "WIN":
            adjusted_risk_pct = min(risk_pct * 1.50, risk_pct * (1.0 + (0.10 * streak)))
        elif streak_type == "LOSS":
            adjusted_risk_pct = max(risk_pct * 0.30, risk_pct * (1.0 - (0.20 * streak)))
            
        # Volatility modifier (Reduce risk by 40% if volatility index is highly expanded)
        curr_price = current_tick["bid"] if action == "SELL" else current_tick["ask"]
        if m1_atr > 0 and curr_price > 0:
            volatility_ratio = m1_atr / (curr_price * 0.0005)
            if volatility_ratio > 1.8:
                adjusted_risk_pct *= 0.60
                
        # Spread expansion modifier (Scale down order sizing if spread is approaching max limit)
        spread_ratio = spread_pts / spread_limit_pts if spread_limit_pts > 0 else 0.0
        if spread_ratio > 0.8:
            adjusted_risk_pct *= 0.50
            
        lot_size = self.calculate_volume(acct_equity, sl_points, sym_info, adjusted_risk_pct, action, curr_price)
        if lot_size < sym_info.volume_min:
            return {"qualified": False, "reason": f"Computed dynamic volume ({lot_size}) below minimum broker allowance."}

        # Calculate exact price points
        tp_ratio = max(tp_mult, 2.0) # Ensure broker-side TP covers partial targets
        if action == "BUY":
            sl_price = curr_price - (sl_points * sym_info.point)
            tp_price = curr_price + (int(sl_points * tp_ratio) * sym_info.point)
        else: # SELL
            sl_price = curr_price + (sl_points * sym_info.point)
            tp_price = curr_price - (int(sl_points * tp_ratio) * sym_info.point)

        # 7. Broker margin validation check
        required_margin = mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
            self.symbol, lot_size, curr_price
        )
        
        state.lock.acquire()
        margin_free = state.margin_free
        state.lock.release()

        if required_margin is None or required_margin > margin_free * 0.8:
            return {"qualified": False, "reason": "Insufficient broker leverage/free margin."}

        return {
            "qualified": True,
            "reason": "Risk verification passed.",
            "lot_size": lot_size,
            "sl": round(sl_price, sym_info.digits),
            "tp": round(tp_price, sym_info.digits),
            "sl_points": sl_points,
            "price": curr_price
        }

    def calculate_volume(self, equity: float, sl_points: int, sym_info: Any, risk_pct: float, action: str, price: float) -> float:
        """Computes trading volume from standard target risk specifications."""
        risk_money = equity * (risk_pct / 100.0)
        risk_per_lot = None
        
        try:
            mt5_action = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
            target_sl_price = price - (sl_points * sym_info.point) if action == "BUY" else price + (sl_points * sym_info.point)
            profit = mt5.order_calc_profit(mt5_action, sym_info.name, 1.0, price, target_sl_price)
            if profit is not None:
                risk_per_lot = abs(profit)
        except Exception as e:
            self.logger.warning(f"Error calling native order_calc_profit: {e}")

        if risk_per_lot is None or risk_per_lot <= 0:
            price_delta = sl_points * sym_info.point
            risk_per_lot = price_delta * sym_info.trade_contract_size

        volume = risk_money / risk_per_lot if risk_per_lot > 0 else 0.0
        steps = round(volume / sym_info.volume_step)
        volume = steps * sym_info.volume_step
        
        # Clamp bounds
        volume = max(sym_info.volume_min, min(sym_info.volume_max, volume))
        return round(volume, 2)
