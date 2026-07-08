import logging
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List
import MetaTrader5 as mt5

from Titan.config.config import get_settings
from Titan.strategies.technical_analysis import TechAnalysis
from Titan.core.decision_engine import DecisionEngine
from Titan.core.smart_entry import SmartEntryEngine
from Titan.market.sessions import SessionManager
from Titan.market.economic_calendar import EconomicCalendar

logger = logging.getLogger("Titan.Backtester")

class Backtester:
    
    @staticmethod
    def run_historical_backtest(
        symbol: str, 
        preset_range: str, 
        start_date: datetime = None, 
        end_date: datetime = None, 
        initial_balance: float = 100000.0
    ) -> Dict[str, Any]:
        """
        Replays combined M1/M3/M5 multi-timeframe candles sequentially.
        Calculates win rate, profit factor, expectancy, average R, max drawdown,
        trade count, and average holding time.
        """
        from Titan.execution.mt5_client import MT5Client
        if not MT5Client.initialize():
            return {"error": "MetaTrader 5 broker terminal connection unavailable"}
            
        settings = get_settings()
        
        # 1. Date range resolution
        utc_now = datetime.now(timezone.utc)
        if preset_range == "last_day":
            end_dt = utc_now
            start_dt = end_dt - timedelta(days=1)
        elif preset_range == "last_week":
            end_dt = utc_now
            start_dt = end_dt - timedelta(days=7)
        elif preset_range == "last_month":
            end_dt = utc_now
            start_dt = end_dt - timedelta(days=30)
        else:
            start_dt = start_date if start_date else (utc_now - timedelta(days=7))
            end_dt = end_date if end_date else utc_now
            
        logger.info(f"Retrieving synchronized multi-timeframe candle data for {symbol} from {start_dt} to {end_dt}...")
        
        # Copy candles for all three frames: M1, M3, M5
        rates_m1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start_dt, end_dt)
        rates_m3 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M3, start_dt - timedelta(hours=10), end_dt)
        rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_dt - timedelta(hours=20), end_dt)
        
        if rates_m1 is None or len(rates_m1) == 0:
            MT5Client.shutdown()
            return {"error": "Failed to retrieve M1 candles data buffer"}
            
        # Parse candles to standard structured list of dicts
        m1_candles_pool = []
        for r in rates_m1:
            m1_candles_pool.append({
                "time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
                "tick_volume": int(r[5]), "spread": int(r[6])
            })
            
        m3_pool = []
        if rates_m3 is not None:
            for r in rates_m3:
                m3_pool.append({
                    "time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])
                })
                
        m5_pool = []
        if rates_m5 is not None:
            for r in rates_m5:
                m5_pool.append({
                    "time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])
                })
                
        logger.info(f"Ingested {len(m1_candles_pool)} execution candles. Initializing replayer logic...")
        
        balance = initial_balance
        equity = initial_balance
        peak_equity = initial_balance
        max_drawdown = 0.0
        
        active_position = None # None or Dict
        closed_trades = []
        
        # We loop through M1 candles sequentially to simulate real historical replay
        for idx in range(100, len(m1_candles_pool)):
            m1_candle = m1_candles_pool[idx]
            current_time = m1_candle["time"]
            current_price = m1_candle["open"]
            spread_val = m1_candle["spread"]
            
            # --- A. Evaluate Active Trade (Multi-tier Take Profit check) ---
            if active_position:
                direction = active_position["direction"]
                entry_p = active_position["entry_price"]
                sl = active_position["sl"]
                tp1 = active_position["tp1"]
                tp2 = active_position["tp2"]
                tp3 = active_position["tp3"]
                lots = active_position["volume"]
                orig_lots = active_position["orig_vol"]
                
                # Check for stop, partials and main limit targets on this candle range
                high = m1_candle["high"]
                low = m1_candle["low"]
                
                # Check Stop Loss
                is_stopped = False
                if direction == "BUY" and low <= sl:
                    is_stopped = True
                elif direction == "SELL" and high >= sl:
                    is_stopped = True
                    
                if is_stopped:
                    # Liquidation of whatever volume remains
                    pnl = (sl - entry_p) * lots * 100.0 if direction == "BUY" else (entry_p - sl) * lots * 100.0
                    active_position["pnl"] += pnl
                    active_position["close_price"] = sl
                    active_position["close_time"] = current_time
                    active_position["status"] = "STOPPED_OUT"
                    active_position["exit_reason"] = "SL hit"
                    active_position["duration_seconds"] = current_time - active_position["open_time"]
                    
                    closed_trades.append(active_position)
                    balance += active_position["pnl"]
                    equity = balance
                    active_position = None
                    
                    # Track drawdown
                    if equity > peak_equity:
                        peak_equity = equity
                    max_drawdown = max(max_drawdown, ((peak_equity - equity) / peak_equity * 100.0))
                    continue
                    
                # Check Take Profits
                if direction == "BUY":
                    # Check TP1 (close 33% and BE)
                    if high >= tp1 and active_position["tp1_status"] == "ACTIVE":
                        close_size = round(orig_lots * 0.33, 2)
                        pnl1 = (tp1 - entry_p) * close_size * 100.0
                        active_position["pnl"] += pnl1
                        active_position["volume"] -= close_size
                        active_position["tp1_status"] = "HIT"
                        # Move SL to Breakeven
                        active_position["sl"] = entry_p + (10 * 0.01) 
                        logger.info(f"Backtest: TP1 hit at {tp1:.3f}. Volume reduced. SL moved to Breakeven.")
                        
                    # Check TP2 (close another 33%)
                    if high >= tp2 and active_position["tp2_status"] == "ACTIVE":
                        close_size = round(orig_lots * 0.33, 2)
                        pnl2 = (tp2 - entry_p) * close_size * 100.0
                        active_position["pnl"] += pnl2
                        active_position["volume"] -= close_size
                        active_position["tp2_status"] = "HIT"
                        logger.info(f"Backtest: TP2 hit at {tp2:.3f}. Volume reduced to runner.")
                        
                    # Check TP3 (exit remaining)
                    if high >= tp3:
                        pnl3 = (tp3 - entry_p) * active_position["volume"] * 100.0
                        active_position["pnl"] += pnl3
                        active_position["close_price"] = tp3
                        active_position["close_time"] = current_time
                        active_position["status"] = "TARGET_HIT"
                        active_position["exit_reason"] = "TP3 hit"
                        active_position["duration_seconds"] = current_time - active_position["open_time"]
                        
                        closed_trades.append(active_position)
                        balance += active_position["pnl"]
                        equity = balance
                        active_position = None
                        
                        # Track drawdown
                        if equity > peak_equity:
                            peak_equity = equity
                        max_drawdown = max(max_drawdown, ((peak_equity - equity) / peak_equity * 100.0))
                        continue
                else: # SELL
                    # Check TP1
                    if low <= tp1 and active_position["tp1_status"] == "ACTIVE":
                        close_size = round(orig_lots * 0.33, 2)
                        pnl1 = (entry_p - tp1) * close_size * 100.0
                        active_position["pnl"] += pnl1
                        active_position["volume"] -= close_size
                        active_position["tp1_status"] = "HIT"
                        active_position["sl"] = entry_p - (10 * 0.01)
                        logger.info(f"Backtest: TP1 hit at {tp1:.3f}. Volume reduced. SL moved to Breakeven.")
                        
                    # Check TP2
                    if low <= tp2 and active_position["tp2_status"] == "ACTIVE":
                        close_size = round(orig_lots * 0.33, 2)
                        pnl2 = (entry_p - tp2) * close_size * 100.0
                        active_position["pnl"] += pnl2
                        active_position["volume"] -= close_size
                        active_position["tp2_status"] = "HIT"
                        logger.info(f"Backtest: TP2 hit at {tp2:.3f}. Volume reduced to runner.")
                        
                    # Check TP3
                    if low <= tp3:
                        pnl3 = (entry_p - tp3) * active_position["volume"] * 100.0
                        active_position["pnl"] += pnl3
                        active_position["close_price"] = tp3
                        active_position["close_time"] = current_time
                        active_position["status"] = "TARGET_HIT"
                        active_position["exit_reason"] = "TP3 hit"
                        active_position["duration_seconds"] = current_time - active_position["open_time"]
                        
                        closed_trades.append(active_position)
                        balance += active_position["pnl"]
                        equity = balance
                        active_position = None
                        
                        # Track drawdown
                        if equity > peak_equity:
                            peak_equity = equity
                        max_drawdown = max(max_drawdown, ((peak_equity - equity) / peak_equity * 100.0))
                        continue
                        
            # --- B. Scan Signals for New Position ---
            if active_position is None:
                # Synchronize M3 and M5 candles up to current M1 timeframe coordinate
                m1_sub = m1_candles_pool[idx-100:idx]
                m3_sub = [c for c in m3_pool if c["time"] < current_time][-100:]
                m5_sub = [c for c in m5_pool if c["time"] < current_time][-100:]
                
                if len(m3_sub) < 30 or len(m5_sub) < 30:
                    continue
                    
                # Evaluate confluences
                confluences = TechAnalysis.analyze_multi_timeframe(m1_sub, m3_sub, m5_sub)
                
                # Check session hour
                dt_utc = datetime.fromtimestamp(current_time, timezone.utc)
                session_info = SessionManager.get_current_sessions(dt_utc)
                session_desc = session_info["session_desc"]
                session_valid = (settings.get("trading_session") == "All") or (settings.get("trading_session") in session_desc)
                
                # News lock
                news_locked, _, _ = EconomicCalendar.check_news_lock(dt_utc)
                
                # Perform scoring
                dec = DecisionEngine.evaluate_setup(
                    confluences, spread_val, news_locked, session_valid, 1.5, settings
                )
                
                action = dec["decision"]
                
                if action in ["BUY", "SELL"] and not news_locked and session_valid:
                    # Entry qualification checks
                    m1_atr = confluences.get("m1_metrics", {}).get("atr_14", 0.0)
                    if m1_atr > 0:
                        # Compute Stop distance
                        sl_points = max(150, min(500, int((m1_atr / 0.01) * settings.get("atr_multiplier", 1.5))))
                        
                        # Dynamic lot sizing
                        risk_money = balance * (settings.get("risk_pct", 1.0) / 100.0)
                        lots = round(risk_money / (sl_points * 0.01 * 100.0), 2)
                        lots = max(0.01, lots)
                        
                        # Smart Entry params
                        entry_params = SmartEntryEngine.calculate_entry_parameters(
                            action, current_price, sl_points, 0.01, m1_atr, dec["score"], confluences.get("macro_trend", "Trending")
                        )
                        
                        active_position = {
                            "symbol": symbol,
                            "direction": action,
                            "open_time": current_time,
                            "entry_price": current_price,
                            "sl": entry_params["stop_loss"],
                            "tp1": entry_params["tp1"],
                            "tp2": entry_params["tp2"],
                            "tp3": entry_params["tp3"],
                            "volume": lots,
                            "orig_vol": lots,
                            "pnl": 0.0,
                            "status": "OPEN",
                            "tp1_status": "ACTIVE",
                            "tp2_status": "ACTIVE",
                            "exit_reason": "",
                            "score": dec["score"],
                            "confidence": dec["confidence"]
                        }
                        
        # 3. Compile Statistics
        MT5Client.shutdown()
        
        trade_count = len(closed_trades)
        if trade_count == 0:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "avg_r": 0.0,
                "max_drawdown": 0.0,
                "sharpe": 0.0,
                "avg_hold_mins": 0.0,
                "trades": []
            }
            
        wins = [t for t in closed_trades if t["pnl"] > 0]
        losses = [t for t in closed_trades if t["pnl"] <= 0]
        
        win_rate = (len(wins) / trade_count) * 100.0
        
        gross_profit = sum([t["pnl"] for t in wins])
        gross_loss = abs(sum([t["pnl"] for t in losses]))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else gross_profit
        
        # Expectancy: (Win% * AvgWin) - (Loss% * AvgLoss)
        avg_win = (gross_profit / len(wins)) if len(wins) > 0 else 0.0
        avg_loss = (gross_loss / len(losses)) if len(losses) > 0 else 0.0
        expectancy = ((win_rate / 100.0) * avg_win) - ((1.0 - (win_rate / 100.0)) * avg_loss)
        
        # Calculate Sharpe Annualized
        pnls = [t["pnl"] for t in closed_trades]
        mean_pnl = np.mean(pnls)
        std_pnl = np.std(pnls) if len(pnls) > 1 else 1.0
        sharpe = (mean_pnl / std_pnl) * np.sqrt(252) if std_pnl > 0 else 0.0
        
        # Average Hold Time minutes
        avg_duration = np.mean([t["duration_seconds"] for t in closed_trades])
        
        # Average R estimate
        # (Average exit - Entry) / (Initial Entry - Initial SL)
        r_multiples = []
        for t in closed_trades:
            # Approx based on dynamic targets
            r_multiples.append(t["pnl"] / 100.0)
        avg_r = np.mean(r_multiples) if r_multiples else 0.0
        
        return {
            "total_trades": trade_count,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "avg_r": round(avg_r, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe": round(sharpe, 2),
            "avg_hold_mins": round(avg_duration / 60.0, 1),
            "final_balance": round(balance, 2),
            "trades": [{
                "open_time": datetime.fromtimestamp(t["open_time"], timezone.utc).strftime('%Y-%m-%d %H:%M'),
                "direction": t["direction"],
                "entry_price": round(t["entry_price"], 3),
                "close_price": round(t["close_price"], 3),
                "pnl": round(t["pnl"], 2),
                "exit_reason": t["exit_reason"]
            } for t in closed_trades]
        }
