import time
import logging
from typing import Dict, Any, List
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, get_settings
from Titan.core.state import state
from Titan.core.logger import trading_logger
from Titan.services.execution_engine import ExecutionEngine
from Titan.market.scanner import MultiTimeframeScanner
from Titan.market.economic_calendar import EconomicCalendar
from Titan.storage.db import get_db_connection

class PositionManager:
    def __init__(self, execution_engine: ExecutionEngine, symbol: str = PRIMARY_SYMBOL):
        self.symbol = symbol
        self.logger = trading_logger
        self.execution_engine = execution_engine

    def adjust_open_positions(self):
        """Monitors and manages open transactions according to individual position scaling/exit rules."""
        state.lock.acquire()
        open_positions = list(state.open_positions)
        emergency_halt = state.emergency_halt
        last_decision_dict = dict(state.latest_decision)
        state.lock.release()

        if not open_positions:
            return

        settings = get_settings()
        last_decision = last_decision_dict.get("decision", "WAIT")
        
        # Connect to SQLite DB for caching original volume and logging exits
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Execute news calendar check
        is_news_locked, _, event_title = EconomicCalendar.check_news_lock()
        
        # Get active execution timeframe to calculate indicators for exits
        exec_tf_str = settings.get("timeframes", {}).get("execution", "M1")
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M3": mt5.TIMEFRAME_M3,
            "M5": mt5.TIMEFRAME_M5,
        }
        exec_tf = tf_map.get(exec_tf_str, mt5.TIMEFRAME_M1)

        for pos in open_positions:
            ticket = pos["ticket"]
            pos_symbol = pos["symbol"]
            volume = pos["volume"]
            direction = pos["type"] # BUY/SELL
            entry = pos["price_open"]
            curr = pos["price_current"]
            sl = pos["sl"]
            tp = pos["tp"]
            
            # Record tick sequence (Rule 1)
            state.lock.acquire()
            try:
                if ticket not in state.tick_sequence_map:
                    state.tick_sequence_map[ticket] = []
                if len(state.tick_sequence_map[ticket]) < 100:
                    state.tick_sequence_map[ticket].append(curr)
            except Exception as e:
                pass
            finally:
                state.lock.release()
            
            sym_info = mt5.symbol_info(pos_symbol)
            if sym_info is None:
                continue
                
            point = sym_info.point
            digits = sym_info.digits

            # 1. Look up original volume
            cursor.execute("SELECT volume FROM trades WHERE ticket = ?", (ticket,))
            db_row = cursor.fetchone()
            if not db_row:
                try:
                    cursor.execute(
                        "INSERT INTO trades (ticket, symbol, direction, volume, entry_price, sl, tp, open_time, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'EXECUTED')",
                        (ticket, pos_symbol, direction, volume, entry, sl, tp)
                    )
                    conn.commit()
                    orig_volume = volume
                except Exception as e:
                    self.logger.error(f"Error caching new open trade {ticket} details: {e}")
                    orig_volume = volume
            else:
                orig_volume = float(db_row["volume"])

            sl_distance = abs(entry - sl) if sl > 0 else (150 * point)
            profit_reached = (curr - entry) if direction == "BUY" else (entry - curr)

            # ── RULE A: EMERGENCY HALT EXIT ──
            if emergency_halt:
                self.logger.warning(f"Position #{ticket} liquidated due to system Emergency Halt.")
                if self.execution_engine.close_position_ticket(ticket, comment="Emergency Halt"):
                    cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='EMERGENCY_HALTED' WHERE ticket=?", (curr, ticket))
                    conn.commit()
                continue

            # ── RULE B: STALE HOLDOVER TIME CAPPED EXIT ──
            max_hold = float(settings.get("max_hold_minutes", 60.0))
            duration_minutes = (time.time() - pos["time"]) / 60.0
            if duration_minutes > max_hold:
                self.logger.info(f"Position #{ticket} exceeded max hold time ({duration_minutes:.1f}m > {max_hold}m). Exiting stale trade...")
                if self.execution_engine.close_position_ticket(ticket, comment="Time Stop Exit"):
                    cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='TIME_STOP' WHERE ticket=?", (curr, ticket))
                    conn.commit()
                continue

            # ── RULE C: NEWS BLACKOUT SHIELDS EXIT ──
            if is_news_locked and settings.get("news_lock", True):
                self.logger.info(f"Position #{ticket} exited before news release: '{event_title}'.")
                if self.execution_engine.close_position_ticket(ticket, comment="News Lock Exit"):
                    cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='NEWS_EXIT' WHERE ticket=?", (curr, ticket))
                    conn.commit()
                continue

            # Copy recent rates for volatility and momentum calculations
            rates = mt5.copy_rates_from_pos(pos_symbol, exec_tf, 0, 15)
            if rates is not None and len(rates) >= 14:
                cls_arr = [r[4] for r in rates]
                hi_arr = [r[2] for r in rates]
                lo_arr = [r[3] for r in rates]
                exec_atr = MultiTimeframeScanner.calculate_atr(hi_arr, lo_arr, cls_arr, 14)
                atr_pts = round(exec_atr / point) if point > 0 else 0
                
                # ── RULE D: VOLATILITY SPIKE EXIT ──
                max_vol_pts = settings.get("max_atr_points_exit", 2000)
                if atr_pts > max_vol_pts:
                    self.logger.warning(f"Position #{ticket} closed on volatility spike: ATR pts {atr_pts} > {max_vol_pts}.")
                    if self.execution_engine.close_position_ticket(ticket, comment="Volatility Spike Exit"):
                        cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='VOLATILITY_EXIT' WHERE ticket=?", (curr, ticket))
                        conn.commit()
                    continue

                # ── RULE E: MOMENTUM EXHAUSTION EXIT ──
                from Titan.market.intelligence.utils import calculate_rsi
                rsi_vals = calculate_rsi(cls_arr, 14)
                if rsi_vals:
                    latest_rsi = rsi_vals[-1]
                    if direction == "BUY" and latest_rsi > 85.0:
                        self.logger.info(f"Closing position #{ticket} on RSI extreme momentum overbought: {latest_rsi:.1f}")
                        if self.execution_engine.close_position_ticket(ticket, comment="RSI Exhaustion Exit"):
                            cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='MOMENTUM_EXIT' WHERE ticket=?", (curr, ticket))
                            conn.commit()
                        continue
                    elif direction == "SELL" and latest_rsi < 15.0:
                        self.logger.info(f"Closing position #{ticket} on RSI extreme momentum oversold: {latest_rsi:.1f}")
                        if self.execution_engine.close_position_ticket(ticket, comment="RSI Exhaustion Exit"):
                            cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='MOMENTUM_EXIT' WHERE ticket=?", (curr, ticket))
                            conn.commit()
                        continue

            # ── RULE F: THESIS INVALIDATION EXIT ──
            if (direction == "BUY" and last_decision == "SELL") or (direction == "SELL" and last_decision == "BUY"):
                self.logger.warning(f"Trade thesis invalidated for position #{ticket}. Signal flipped to {last_decision}.")
                if self.execution_engine.close_position_ticket(ticket, comment="Thesis Invalidation"):
                    cursor.execute("UPDATE trades SET status='CLOSED', close_price=?, close_time=CURRENT_TIMESTAMP, exit_reason='THESIS_INVALIDATED' WHERE ticket=?", (curr, ticket))
                    conn.commit()
                continue

            # ── RULE G: LEVEL EXITS & STOP MODIFICATIONS ──
            closed_fraction = (orig_volume - volume) / orig_volume if orig_volume > 0 else 0.0

            # TP1: Close 33% volume and move SL to BE + positive buffer (10 points / 1 pip)
            if profit_reached >= sl_distance:
                if closed_fraction < 0.25:
                    close_lot = round(orig_volume * 0.33, 2)
                    close_lot = max(sym_info.volume_min, round(close_lot / sym_info.volume_step) * sym_info.volume_step)
                    
                    if volume - close_lot >= sym_info.volume_min:
                        self.logger.info(f"Target TP1 hit. Closing 33% volume ({close_lot} lots) for ticket #{ticket}")
                        if self.execution_engine.close_position_ticket(ticket, volume=close_lot, comment="TP1 Exit (33%)"):
                            # Update local tracker
                            volume -= close_lot
                            
                    # Move SL to Breakeven + positive buffer (+10 points)
                    is_sl_at_be = (sl >= entry) if direction == "BUY" else (sl <= entry)
                    if not is_sl_at_be:
                        buffer = 10 * point
                        new_sl = round(entry + buffer if direction == "BUY" else entry - buffer, digits)
                        self.logger.info(f"Moving SL to Breakeven + buffer: Ticket #{ticket} -> {new_sl}")
                        mt5.order_send({
                            "action": mt5.TRADE_ACTION_SLTP,
                            "symbol": pos_symbol,
                            "sl": float(new_sl),
                            "tp": float(tp),
                            "position": int(ticket)
                        })
                        sl = new_sl

            # TP2: Close second 33% (yielding total 66% out)
            if profit_reached >= (1.5 * sl_distance):
                if closed_fraction < 0.55:
                    close_lot = round(orig_volume * 0.33, 2)
                    close_lot = max(sym_info.volume_min, round(close_lot / sym_info.volume_step) * sym_info.volume_step)
                    
                    if volume - close_lot >= sym_info.volume_min:
                        self.logger.info(f"Target TP2 hit. Closing second 33% volume ({close_lot} lots) for ticket #{ticket}")
                        if self.execution_engine.close_position_ticket(ticket, volume=close_lot, comment="TP2 Exit (66%)"):
                            volume -= close_lot

            # ── RULE H: DYNAMIC TRAILING STOP (based on ATR) ──
            if profit_reached >= sl_distance and rates is not None and len(rates) >= 14:
                atr_mult = settings.get("atr_multiplier", 1.5)
                trail_dist = max(150 * point, exec_atr * atr_mult)
                
                if direction == "BUY":
                    target_sl = round(curr - trail_dist, digits)
                    if target_sl > sl:
                        self.logger.info(f"Trailing SL higher for ticket #{ticket} -> {target_sl}")
                        mt5.order_send({
                            "action": mt5.TRADE_ACTION_SLTP,
                            "symbol": pos_symbol,
                            "sl": float(target_sl),
                            "tp": float(tp),
                            "position": int(ticket)
                        })
                        sl = target_sl
                else: # SELL
                    target_sl = round(curr + trail_dist, digits)
                    if sl == 0.0 or target_sl < sl:
                        self.logger.info(f"Trailing SL lower for ticket #{ticket} -> {target_sl}")
                        mt5.order_send({
                            "action": mt5.TRADE_ACTION_SLTP,
                            "symbol": pos_symbol,
                            "sl": float(target_sl),
                            "tp": float(tp),
                            "position": int(ticket)
                        })
                        sl = target_sl

        conn.commit()
        conn.close()
