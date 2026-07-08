import logging
import time
from typing import Dict, Any, List
from datetime import datetime, timezone
import MetaTrader5 as mt5

from Titan.config.config import get_settings
from Titan.market.scanner import MultiTimeframeScanner
from Titan.market.economic_calendar import EconomicCalendar

logger = logging.getLogger("Titan.PositionManager")

class PositionManager:
    
    @staticmethod
    def get_original_volume(cursor, ticket: int) -> float:
        cursor.execute("SELECT volume FROM trades WHERE ticket = ?", (ticket,))
        row = cursor.fetchone()
        return float(row["volume"]) if row else 0.0

    @staticmethod
    def adjust_open_positions(conn, mt5_client):
        """
        Monitors and manages open transactions according to Phase 3 trade manager rules and Phase 6 requirements:
        1. Emergency Exit Check (Fast liquidation)
        2. Stale Trade / Time Stop Exit Check (MaxHoldMinutes)
        3. News Blackout Guard Exit Check
        4. Volatility Spike Safeguard
        5. Momentum (RSI) Exhaustion Exit
        6. Thesis Invalidation Check (opposite CHoCH or counter-trend signal)
        7. Break-even shifting with positive buffers
        8. Dynamic trailing stop based on ATR
        9. Multi-tier partial profit taking (e.g. 33% or 50% exits)
        """
        open_positions = mt5_client.get_open_positions()
        if not open_positions:
            return
            
        settings = get_settings()
        cursor = conn.cursor()
        
        # We query the latest decision to verify if the opposite thesis is active
        cursor.execute("SELECT decision FROM decisions ORDER BY id DESC LIMIT 1")
        last_decision_row = cursor.fetchone()
        last_decision = last_decision_row["decision"] if last_decision_row else "WAIT"
        
        # Get active execution timeframe to compute indicators
        tf_config = settings.get("timeframes", {})
        exec_tf_str = tf_config.get("execution", "M1")
        TF_MAP = {
            "M1": mt5.TIMEFRAME_M1,
            "M3": mt5.TIMEFRAME_M3,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1
        }
        exec_tf = TF_MAP.get(exec_tf_str, mt5.TIMEFRAME_M1)
        
        # Check News Lockout status
        is_news_locked, _, event_title = EconomicCalendar.check_news_lock()
        
        for pos in open_positions:
            ticket = pos["ticket"]
            symbol = pos["symbol"]
            volume = pos["volume"]
            direction = pos["type"] # BUY/SELL
            entry = pos["price_open"]
            curr = pos["price_current"]
            sl = pos["sl"]
            tp = pos["tp"]
            
            # Fetch symbol metadata
            sym_info = mt5_client.get_symbol_info(symbol)
            if sym_info is None:
                continue
                
            point = sym_info.point
            digits = sym_info.digits
            
            # Original volume lookup from local database
            cursor.execute("SELECT volume, sl, tp FROM trades WHERE ticket = ?", (ticket,))
            db_record = cursor.fetchone()
            
            if not db_record:
                # Insert if not present
                try:
                    cursor.execute(
                        """
                        INSERT INTO trades (ticket, symbol, direction, volume, entry_price, sl, tp, open_time, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTED')
                        """,
                        (ticket, symbol, direction, volume, entry, sl, tp, 
                         time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(pos["time"])))
                    )
                    conn.commit()
                    orig_volume = volume
                except Exception as e:
                    logger.error(f"Error indexing position {ticket}: {e}")
                    orig_volume = volume
            else:
                orig_volume = float(db_record["volume"])
                
            # Compute target tiers
            sl_distance = abs(entry - sl) if sl > 0 else (150 * point)
            
            # Profit/loss metrics
            if direction == "BUY":
                profit_reached = curr - entry
            else: # SELL
                profit_reached = entry - curr
                
            # --- 1. Emergency Exit Check ---
            if settings.get("emergency_halt", False):
                logger.warning(f"Liquidating position {ticket} due to Emergency Halt status.")
                mt5_client.close_position(ticket, comment="Emergency Halt Exit")
                cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='EMERGENCY_HALTED' WHERE ticket=?", (curr, ticket))
                conn.commit()
                continue
                
            # --- 2. Stale Trade / Time Stop Exit Check ---
            duration_minutes = (time.time() - pos["time"]) / 60.0
            max_hold = settings.get("max_hold_minutes", 60.0)
            if duration_minutes > max_hold:
                logger.info(f"Position {ticket} exceeded max hold time ({duration_minutes:.1f}m > {max_hold}m). Closing stale trade.")
                mt5_client.close_position(ticket, comment="Time Stop Exit")
                cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='TIME_STOP' WHERE ticket=?", (curr, ticket))
                conn.commit()
                continue

            # --- 3. News Blackout Guard Exit Check ---
            if is_news_locked and settings.get("news_lock", True):
                logger.info(f"Position {ticket} liquidated before major news release: '{event_title}'.")
                mt5_client.close_position(ticket, comment="News Lockout Exit")
                cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='NEWS_EXIT' WHERE ticket=?", (curr, ticket))
                conn.commit()
                continue
                
            # Fetch latest Exec timeframe indicators for Volatility and Momentum check
            rates = mt5.copy_rates_from_pos(symbol, exec_tf, 0, 15)
            if rates is not None and len(rates) > 2:
                cls_arr = [r[4] for r in rates]
                hi_arr = [r[2] for r in rates]
                lo_arr = [r[3] for r in rates]
                exec_atr = MultiTimeframeScanner.calculate_atr(hi_arr, lo_arr, cls_arr, 14)
                atr_pts = round(exec_atr / point) if point > 0 else 0
                
                # --- 4. Volatility Exit Check ---
                max_vol_pts = settings.get("max_atr_points_exit", 250)
                if atr_pts > max_vol_pts:
                    logger.warning(f"Position {ticket} closed due to excessive volatility spike: ATR pts {atr_pts} > {max_vol_pts}.")
                    mt5_client.close_position(ticket, comment="Volatility Spike Exit")
                    cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='VOLATILITY_EXIT' WHERE ticket=?", (curr, ticket))
                    conn.commit()
                    continue
                
                # --- 5. Momentum Exit (RSI extreme exhaustion) Check ---
                from Titan.market.intelligence.utils import calculate_rsi
                rsi_vals = calculate_rsi(cls_arr, 14)
                if rsi_vals:
                    latest_rsi = rsi_vals[-1]
                    if direction == "BUY" and latest_rsi > 85.0:
                        logger.info(f"Position {ticket} closing at extreme overbought momentum (RSI = {latest_rsi:.1f}).")
                        mt5_client.close_position(ticket, comment="RSI Overbought Exhaustion")
                        cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='MOMENTUM_EXIT' WHERE ticket=?", (curr, ticket))
                        conn.commit()
                        continue
                    elif direction == "SELL" and latest_rsi < 15.0:
                        logger.info(f"Position {ticket} closing at extreme oversold momentum (RSI = {latest_rsi:.1f}).")
                        mt5_client.close_position(ticket, comment="RSI Oversold Exhaustion")
                        cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='MOMENTUM_EXIT' WHERE ticket=?", (curr, ticket))
                        conn.commit()
                        continue

            # --- 6. Thesis Invalidation Check ---
            if (direction == "BUY" and last_decision == "SELL") or (direction == "SELL" and last_decision == "BUY"):
                logger.warning(f"Trade thesis invalidated for position {ticket} due to reverse signal ({last_decision}). Exiting instantly.")
                mt5_client.close_position(ticket, comment="Titan Thesis Inval Exit")
                cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='THESIS_INVALIDATED' WHERE ticket=?", (curr, ticket))
                conn.commit()
                continue
                
            # --- 7. Level Exits & Stop modifications ---
            closed_fraction = (orig_volume - volume) / orig_volume if orig_volume > 0 else 0.0
            
            # Check TP1 - Close 33% and move SL to BE
            if profit_reached >= sl_distance:
                if closed_fraction < 0.25:
                    close_lot = round(orig_volume * 0.33, 2)
                    close_lot = max(sym_info.volume_min, round(close_lot / sym_info.volume_step) * sym_info.volume_step)
                    
                    if volume - close_lot >= sym_info.volume_min:
                        logger.info(f"Target TP1 hit. Executing 33% partial close ({close_lot} lots) for ticket {ticket}")
                        succ = mt5_client.close_position(ticket, volume=close_lot, comment="Titan TP1 Exit (33%)")
                        if succ:
                            volume -= close_lot
                            
                    # Move SL to Breakeven
                    is_sl_at_be = (sl >= entry) if direction == "BUY" else (sl <= entry)
                    if not is_sl_at_be:
                        new_sl = round(entry + (10 * point) if direction == "BUY" else entry - (10 * point), digits)
                        logger.info(f"Moving SL to Breakeven: ticket={ticket}, sl={new_sl}")
                        mt5_client.modify_sl_tp(ticket, new_sl, tp)
                        sl = new_sl
                        
            # Check TP2 - Close another 33%
            if profit_reached >= (1.5 * sl_distance):
                if closed_fraction < 0.55:
                    close_lot = round(orig_volume * 0.33, 2)
                    close_lot = max(sym_info.volume_min, round(close_lot / sym_info.volume_step) * sym_info.volume_step)
                    
                    if volume - close_lot >= sym_info.volume_min:
                        logger.info(f"Target TP2 hit. Executing second 33% partial close ({close_lot} lots) for ticket {ticket}")
                        succ = mt5_client.close_position(ticket, volume=close_lot, comment="Titan TP2 Exit (66%)")
                        if succ:
                            volume -= close_lot
                            
            # --- 8. Dynamic Trailing Stop ---
            if profit_reached >= sl_distance and rates is not None and len(rates) > 2:
                atr_mult = settings.get("atr_multiplier", 1.5)
                trail_dist = max(150 * point, exec_atr * atr_mult)
                
                if direction == "BUY":
                    target_sl = round(curr - trail_dist, digits)
                    if target_sl > sl:
                        logger.info(f"Trailing SL higher for ticket {ticket} to {target_sl}")
                        mt5_client.modify_sl_tp(ticket, target_sl, tp)
                        sl = target_sl
                else: # SELL
                    target_sl = round(curr + trail_dist, digits)
                    if sl == 0.0 or target_sl < sl:
                        logger.info(f"Trailing SL lower for ticket {ticket} to {target_sl}")
                        mt5_client.modify_sl_tp(ticket, target_sl, tp)
                        sl = target_sl
                            
        conn.commit()
