import logging
import numpy as np
from datetime import datetime, timezone
from typing import Dict, Any, List
import MetaTrader5 as mt5

from Titan.config.config import get_settings, PRIMARY_SYMBOL
from Titan.market.economic_calendar import EconomicCalendar
from Titan.market.sessions import SessionManager

logger = logging.getLogger("Titan.QualificationEngine")

class QualificationEngine:
    
    @staticmethod
    def calculate_position_size(
        account_equity: float, 
        sl_points: int, 
        symbol_info: Any, 
        risk_pct: float,
        action: str = "BUY",
        curr_price: float = 0.0
    ) -> float:
        """
        Calculates position sizing dynamically based on equity, risk percentage, and SL points distance.
        Enforces lot limits.
        """
        if sl_points <= 0:
            return 0.0
            
        risk_money = account_equity * (risk_pct / 100.0)
        
        # Determine risk per lot using native MT5 calculation or contract size fallback
        risk_per_lot = None
        
        # Try native mt5.order_calc_profit first if price is available
        if curr_price > 0:
            try:
                mt5_action = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
                target_sl_price = curr_price - (sl_points * symbol_info.point) if action == "BUY" else curr_price + (sl_points * symbol_info.point)
                profit = mt5.order_calc_profit(mt5_action, symbol_info.name, 1.0, curr_price, target_sl_price)
                if profit is not None:
                    risk_per_lot = abs(profit)
            except Exception as e:
                logger.warning(f"Failed native order_calc_profit calculation: {e}")
                
        # Fallback to mathematical representation using contract size
        if risk_per_lot is None or risk_per_lot <= 0:
            price_delta = sl_points * symbol_info.point
            risk_per_lot = price_delta * symbol_info.trade_contract_size
            
        # Calculate volume
        volume = risk_money / risk_per_lot if risk_per_lot > 0 else 0.0
        
        volume_min = symbol_info.volume_min
        volume_max = symbol_info.volume_max
        volume_step = symbol_info.volume_step
        
        steps = round(volume / volume_step)
        volume = steps * volume_step
        
        if volume < volume_min:
            volume = volume_min
        if volume > volume_max:
            volume = volume_max
            
        return round(volume, 2)

    @staticmethod
    def qualify_trade(
        symbol: str, 
        action: str, 
        mt5_client: Any, 
        conn_db: Any, 
        current_tick: Dict[str, float], 
        m1_candles: List[Dict[str, Any]],
        confluence_payload: Dict[str, Any],
        decision_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Executes strict institutional trade qualification checks.
        If ANY check fails, rejects the setup.
        """
        # Load active settings database overrides
        settings = get_settings()
        
        # 1. Trend Alignment Check
        # Alignment check requires that execution, confirmation and trend scales point to same direction
        if not confluence_payload.get("trend_aligned", False):
            return {"qualified": False, "reason": "Trend mismatch across timeframes (M1/M3/M5)"}
            
        # 2. News Lock Check
        is_news_locked, minutes_left, event_title = EconomicCalendar.check_news_lock()
        if settings.get("news_lock", True) and is_news_locked:
            return {"qualified": False, "reason": f"News lock filter: '{event_title}' active ({minutes_left:.1f}m left)"}
            
        # 3. Acceptable Spread Check
        spread = current_tick["spread"]
        symbol_info = mt5_client.get_symbol_info(symbol)
        if symbol_info is None:
            return {"qualified": False, "reason": "Symbol specifications query failed"}
            
        spread_pts = round(spread / symbol_info.point)
        spread_limit_pts = settings.get("spread_limit", 300)
        if spread_pts > spread_limit_pts:
            return {"qualified": False, "reason": f"Spread too wide ({spread_pts} pts) > Limit ({spread_limit_pts} pts)"}
            
        # 4. Sufficient Volatility Check (ATR is sufficient)
        m1_atr = confluence_payload.get("m1_metrics", {}).get("atr_14", 0.0)
        # Adaptive volatility filter based on execution timeframe (avoid blocking good M1/M3 setups)
        exec_tf_str = settings.get("timeframes", {}).get("execution", "M1")
        if exec_tf_str == "M1":
            min_atr_pts = 20  # 2.0 pips / $0.20 on Gold on M1 is fully tradeable
        elif exec_tf_str == "M3":
            min_atr_pts = 30  # 3.0 pips / $0.30 on M3
        else:
            min_atr_pts = 50  # 5.0 pips / $0.50 on M5 and above
            
        atr_pts = round(m1_atr / symbol_info.point) if m1_atr > 0 else 0
        if atr_pts < min_atr_pts:
            return {"qualified": False, "reason": f"Insufficient market volatility ({exec_tf_str} ATR = {atr_pts} pts) < Minimum ({min_atr_pts} pts)"}
            
        # 5. Mindful Risk/Reward & Confidence Level
        min_confidence = settings.get("confidence_threshold", 0.70)
        actual_confidence = decision_payload.get("confidence", 0.0)
        if actual_confidence < min_confidence:
            return {"qualified": False, "reason": f"Signal confidence ({actual_confidence:.2f}) below threshold ({min_confidence:.2f})"}
            
        # 6. Session Allowed Check
        sess_data = SessionManager.get_current_sessions()
        configured_session = settings.get("trading_session", "London-New York Overlap")
        
        # If specific session configured, check if active
        if configured_session != "All":
            session_active = False
            for act_sess in sess_data["active_sessions"]:
                if configured_session.lower() in act_sess.lower() or act_sess.lower() in configured_session.lower():
                    session_active = True
                    break
            # Check overlaps too
            for ov_sess in sess_data["overlaps"]:
                if configured_session.lower() in ov_sess.lower() or ov_sess.lower() in configured_session.lower():
                    session_active = True
                    break
            if not session_active:
                return {"qualified": False, "reason": f"Trading hours restrict. System active only for: '{configured_session}'"}
 
        # 7. Max Concurrent Trades Limit
        active_positions = mt5_client.get_open_positions()
        max_positions = settings.get("max_concurrent_positions", 2)
        if len(active_positions) >= max_positions:
            return {"qualified": False, "reason": f"Maximum open position limits ({max_positions}) reached"}
            
        # 8. Duplicate Trade Check
        for pos in active_positions:
            if pos["symbol"] == symbol and pos["type"] == action:
                return {"qualified": False, "reason": f"Duplicate position already active for {symbol} ({action})"}
 
        # 9. Correlation Check
        for pos in active_positions:
            pos_symbol = pos["symbol"]
            pos_type = pos["type"]
            # If trading gold/currencies, check for cross-asset USD/XAU correlation
            if pos_symbol != symbol:
                is_usd_pair = ("USD" in pos_symbol and "USD" in symbol)
                is_xau_metal = (("XAU" in pos_symbol or "GOLD" in pos_symbol) and ("XAU" in symbol or "GOLD" in symbol))
                if (is_usd_pair or is_xau_metal) and pos_type == action:
                    return {"qualified": False, "reason": f"Correlation Check Failed: Existing {pos_symbol} ({pos_type}) direction correlates positively with {symbol} ({action})"}
                    
        # 10. Daily Drawdown check
        acct = mt5_client.get_account_info()
        if acct is None:
            return {"qualified": False, "reason": "Failed to read MT5 account statistics during risk verification"}
            
        today_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        cursor = conn_db.cursor()
        cursor.execute(
            "SELECT SUM(pnl) as today_pnl FROM trades WHERE status='CLOSED' AND close_time >= ?",
            (today_date + " 00:00:00",)
        )
        row = cursor.fetchone()
        today_pnl = row["today_pnl"] if row and row["today_pnl"] is not None else 0.0
        
        daily_loss_limit = settings.get("max_daily_loss", 3.0)
        max_allowed_loss_money = acct["equity"] * (daily_loss_limit / 100.0)
        if today_pnl < -max_allowed_loss_money:
            return {"qualified": False, "reason": f"Max daily loss limit exceeded: Today Net PNL = {today_pnl:.2f} (Limit = -{max_allowed_loss_money:.2f})"}
            
        # 10. Dynamic ATR-based Stop Loss & Position Sizing
        # Under settings, atr_multiplier determines stop distance
        atr_mult = settings.get("atr_multiplier", 1.5)
        
        # Enforce dynamic stops based on Exec ATR
        atr_points = round(m1_atr / symbol_info.point) if m1_atr > 0 else 150
        sl_points = max(150, min(500, int(atr_points * atr_mult))) # Capped min 15 pips, max 50 pips
        
        curr_price = current_tick["bid"] if action == "SELL" else current_tick["ask"]
        
        # Determine Stop and TP prices
        tp_mult = settings.get("tp_multiplier", 1.5)
        
        # Let's ensure the broker-side TP has enough margin for partial targets (TP1 & TP2) to execute
        tp_ratio = max(tp_mult, 2.0)
        
        if action == "BUY":
            sl_price = curr_price - (sl_points * symbol_info.point)
            tp_points = int(sl_points * tp_ratio)
            tp_price = curr_price + (tp_points * symbol_info.point)
        else: # SELL
            sl_price = curr_price + (sl_points * symbol_info.point)
            tp_points = int(sl_points * tp_ratio)
            tp_price = curr_price - (tp_points * symbol_info.point)
            
        # Calculate Position Size (Dynamic Lot sizing)
        risk_pct = settings.get("risk_pct", 1.0)
        lot_size = QualificationEngine.calculate_position_size(
            acct["equity"], sl_points, symbol_info, risk_pct, action, curr_price
        )
        
        if lot_size < symbol_info.volume_min:
            return {"qualified": False, "reason": f"Lot size computed ({lot_size}) below minimum broker allowed limit ({symbol_info.volume_min})"}
            
        # Margin Check
        required_margin = mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
            symbol, lot_size, curr_price
        )
        if required_margin is None or required_margin > acct["margin_free"] * 0.8:
            return {"qualified": False, "reason": "Execution rejected: Insufficient margin to carry sizing"}
            
        return {
            "qualified": True,
            "reason": "All institutional criteria verified. Sizing qualified.",
            "lot_size": lot_size,
            "sl": round(sl_price, symbol_info.digits),
            "tp": round(tp_price, symbol_info.digits),
            "sl_points": sl_points,
            "raw_price": curr_price
        }
