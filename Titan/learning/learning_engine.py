import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any
import numpy as np
import sqlite3
import MetaTrader5 as mt5

from Titan.config.config import DB_PATH

logger = logging.getLogger("Titan.LearningEngine")

class LearningEngine:
    
    @staticmethod
    def capture_trade_lifetime_extremes(symbol: str, open_time_str: str, close_time_str: str, entry_price: float, direction: str, mt5_client: Any) -> tuple:
        """
        Calculates the exact Maximum Favorable Excursion (MFE) and Maximum Adverse Excursion (MAE)
        by copying M1 ticks/candles from MetaTrader 5 between the open and close timestamps.
        """
        if not mt5_client.check_connection():
            return 0.0, 0.0
            
        try:
            open_dt = datetime.strptime(open_time_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            close_dt = datetime.strptime(close_time_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except Exception as e:
            logger.error(f"Failed parsing timestamps for excursions calculations: {e}")
            return 0.0, 0.0
            
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, open_dt, close_dt)
        if rates is None or len(rates) == 0:
            return 0.0, 0.0
            
        highs = [r[2] for r in rates]
        lows = [r[3] for r in rates]
        
        max_high = max(highs)
        min_low = min(lows)
        
        if direction.upper() in ["BUY", "BUY_DEAL", "0", "ORDER_TYPE_BUY", "0.0"]:
            mfe = max_high - entry_price
            mae = entry_price - min_low
        else: # SELL
            mfe = entry_price - min_low
            mae = max_high - entry_price
            
        return max(0.0, mfe), max(0.0, mae)

    @staticmethod
    def process_completed_trades(conn, mt5_client):
        """
        Syncs recently closed broker deals with the local DB trades store,
        enriching the records with session times, MAE/MFE, timeframes, and setups.
        Rebuilds dynamic history matching MT5 deals, deleting stale cached lines.
        """
        if not mt5_client.check_connection():
            return
            
        closed_deals = mt5_client.get_closed_trades(days=60)
        cursor = conn.cursor()
        
        mt5_tickets = []
        for deal in closed_deals:
            ticket = deal["ticket"]
            mt5_tickets.append(ticket)
            symbol = deal["symbol"]
            direction = deal["direction"]
            volume = deal["volume"]
            close_price = deal["close_price"]
            pnl = deal["pnl"]
            close_time_str = deal["time"]
            
            # Verify if already logged
            cursor.execute("SELECT status, open_time, entry_price, regime_at_opening, exit_reason FROM trades WHERE ticket = ?", (ticket,))
            db_row = cursor.fetchone()
            
            if db_row and db_row["status"] == "CLOSED":
                continue
                
            open_time_str = db_row["open_time"] if db_row else close_time_str
            regime = db_row["regime_at_opening"] if db_row and db_row["regime_at_opening"] else "Trending"
            exit_reason = db_row["exit_reason"] if db_row and db_row["exit_reason"] else "Target Hit"
            
            if db_row:
                entry = db_row["entry_price"]
            else:
                mult = 100.0 if "XAU" in symbol or "GOLD" in symbol else 100000.0
                entry = close_price - (pnl / (volume * mult)) if (direction == "BUY" and (volume * mult) > 0) else close_price + (pnl / (volume * mult)) if ((volume * mult) > 0) else close_price
            
            # Dynamic MAE/MFE excursions
            mfe, mae = LearningEngine.capture_trade_lifetime_extremes(
                symbol, open_time_str, close_time_str, entry, direction, mt5_client
            )
            
            sym_info = mt5_client.get_symbol_info(symbol)
            point = sym_info.point if sym_info else 0.01
            mfe_pts = round(mfe / point) if point else 0
            mae_pts = round(mae / point) if point else 0
            
            # Map session
            from Titan.market.sessions import SessionManager
            try:
                open_dt = datetime.strptime(open_time_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                sess = SessionManager.get_current_sessions(open_dt)["session_desc"]
            except Exception:
                sess = "NY Session"
                
            # Cross-reference decisions table to extract indicator snapshots, entry reason & confidence levels
            cursor.execute(
                "SELECT timeframe, reason, confidence, evidence_json FROM decisions WHERE symbol = ? AND timestamp <= ? ORDER BY id DESC LIMIT 1",
                (symbol, open_time_str)
            )
            dec_row = cursor.fetchone()
            
            entry_reason = "Confluence agreement"
            confidence = 0.70
            indicators_json = "{}"
            timeframe = "M1"
            setup_name = "Confluence Breakout"
            
            if dec_row:
                timeframe = dec_row["timeframe"]
                entry_reason = dec_row["reason"]
                setup_name = dec_row["reason"]
                confidence = dec_row["confidence"]
                indicators_json = dec_row["evidence_json"]
                
                try:
                    payload = json.loads(indicators_json)
                    regime = payload.get("regime", {}).get("regime", regime)
                except Exception:
                    pass
            
            # Calculate duration
            try:
                t1 = datetime.strptime(open_time_str, '%Y-%m-%d %H:%M:%S')
                t2 = datetime.strptime(close_time_str, '%Y-%m-%d %H:%M:%S')
                duration_sec = int((t2 - t1).total_seconds())
            except Exception:
                duration_sec = 600
                
            # Perform update
            cursor.execute(
                """
                UPDATE trades 
                SET status = 'CLOSED', close_price = ?, close_time = ?, pnl = ?, exit_reason = ?,
                    gross_pnl = ?, net_pnl = ?, duration = ?, confidence_at_entry = ?
                WHERE ticket = ?
                """,
                (close_price, close_time_str, pnl, exit_reason, deal["profit"], pnl, duration_sec, confidence, ticket)
            )
            
            if not db_row:
                cursor.execute(
                    """
                    INSERT INTO trades (ticket, symbol, direction, volume, entry_price, sl, tp, open_time, status, close_price, close_time, pnl, exit_reason, gross_pnl, net_pnl, duration, confidence_at_entry, strategy_name)
                    VALUES (?, ?, ?, ?, ?, 0.0, 0.0, ?, 'CLOSED', ?, ?, ?, ?, ?, ?, ?, ?, 'Titan Scalper')
                    """,
                    (ticket, symbol, direction, volume, entry, open_time_str, close_price, close_time_str, pnl, exit_reason, deal["profit"], pnl, duration_sec, confidence)
                )
                
            # Determine exit metrics (Rule 1)
            exit_reason_upper = exit_reason.upper() if exit_reason else ""
            sl_hit_val = 1 if "SL" in exit_reason_upper or "STOP LOSS" in exit_reason_upper else 0
            tp_hit_val = 1 if "TP" in exit_reason_upper or "TAKE PROFIT" in exit_reason_upper or "TARGET" in exit_reason_upper else 0
            smart_exit_val = 1 if "SMART" in exit_reason_upper or "EXHAUST" in exit_reason_upper or "LIQUIDATION" in exit_reason_upper else 0
            manual_exit_val = 1 if "MANUAL" in exit_reason_upper or "HALT" in exit_reason_upper else 0
            
            try:
                ind_payload = json.loads(indicators_json) if isinstance(indicators_json, str) else indicators_json
                if not isinstance(ind_payload, dict):
                    ind_payload = {}
            except Exception:
                ind_payload = {}
                
            entry_score_val = int(ind_payload.get("score", 70))
            
            # Root Cause / Winning Analyzers (Rule 2 & 3)
            root_cause_val = "{}"
            win_analysis_val = "{}"
            if pnl < 0:
                rc_analysis = LearningEngine.perform_root_cause_analysis(pnl, regime, sess, ind_payload, mfe_pts, mae_pts, duration_sec)
                root_cause_val = json.dumps(rc_analysis)
            else:
                w_analysis = LearningEngine.perform_winning_trade_analysis(pnl, regime, sess, ind_payload)
                win_analysis_val = json.dumps(w_analysis)
                
            # Retrieve screenshots and ticks from global state thread-safely
            from Titan.core.state import state
            screenshot_e = ""
            screenshot_x = ""
            ticks_seq = []
            
            state.lock.acquire()
            try:
                screenshot_e = state.screenshot_entry_map.get(ticket, "")
                screenshot_x = state.screenshot_exit_map.get(ticket, "")
                ticks_seq = state.tick_sequence_map.get(ticket, [])
            except Exception:
                pass
            finally:
                state.lock.release()
                
            if not screenshot_e:
                screenshot_e = LearningEngine.generate_trade_screenshot(ticket, "ENTRY", symbol, entry, direction)
                state.lock.acquire()
                state.screenshot_entry_map[ticket] = screenshot_e
                state.lock.release()
                
            if not screenshot_x:
                screenshot_x = LearningEngine.generate_trade_screenshot(ticket, "EXIT", symbol, close_price, direction)
                state.lock.acquire()
                state.screenshot_exit_map[ticket] = screenshot_x
                state.lock.release()
                
            # Store learning snapshot
            learning_snapshot = {
                "market_snapshot": {"regime": regime, "session": sess, "symbol": symbol},
                "indicators": ind_payload,
                "entry_reason": entry_reason,
                "exit_reason": exit_reason,
                "confidence": confidence,
                "profit": max(0.0, pnl),
                "loss": abs(min(0.0, pnl))
            }
            
            cursor.execute(
                """
                INSERT OR REPLACE INTO learning_outcomes (
                    ticket, symbol, regime, session, pnl, mfe, mae, duration_seconds, timeframe, setup_name, indicators_json,
                    entry_score, sl_hit, tp_hit, manual_exit, smart_exit, screenshot_entry, screenshot_exit, tick_sequence_json,
                    root_cause_json, win_analysis_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket, symbol, regime, sess, pnl, mfe_pts, mae_pts, duration_sec, timeframe, setup_name, json.dumps(learning_snapshot),
                    entry_score_val, sl_hit_val, tp_hit_val, manual_exit_val, smart_exit_val, screenshot_e, screenshot_x, json.dumps(ticks_seq),
                    root_cause_val, win_analysis_val
                )
            )
            
        # Rebuild History equivalence: Delete any table closed trades that DO NOT exist on MT5 broker history
        if mt5_tickets:
            placeholders = ",".join(["?"] * len(mt5_tickets))
            cursor.execute(
                f"DELETE FROM trades WHERE status='CLOSED' AND ticket NOT IN ({placeholders})",
                tuple(mt5_tickets)
            )
        else:
            cursor.execute("DELETE FROM trades WHERE status='CLOSED'")
            
        conn.commit()

    @staticmethod
    def analyze_performance(conn) -> Dict[str, Any]:
        """
        Compiles detailed performance metrics:
        Win Rate, Profit Factor, Expectancy, Avg Winner/Loser, Avg Hold Time, Max Drawdown,
        Best/Worst Sessions, Timeframes, and Setups.
        Generates tactical parameter recommendations based on statistically significant outcomes.
        """
        cursor = conn.cursor()
        cursor.execute("SELECT regime, session, pnl, mfe, mae, duration_seconds, indicators_json, timeframe, setup_name FROM learning_outcomes")
        rows = cursor.fetchall()
        
        if not rows:
            return {
                "status": "insufficient_data", 
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "avg_winner": 0.0,
                "avg_loser": 0.0,
                "avg_hold_time_seconds": 0.0,
                "max_drawdown": 0.0,
                "best_session": "N/A",
                "worst_session": "N/A",
                "best_timeframe": "N/A",
                "worst_timeframe": "N/A",
                "best_setup": "N/A",
                "worst_setup": "N/A",
                "recommendations": []
            }
            
        total = len(rows)
        wins = [float(r["pnl"]) for r in rows if float(r["pnl"]) > 0]
        losses = [float(r["pnl"]) for r in rows if float(r["pnl"]) < 0]
        
        total_pnl = sum([float(r["pnl"]) for r in rows])
        win_rate = (len(wins) / total * 100.0) if total > 0 else 0.0
        
        sum_wins = sum(wins)
        sum_losses = sum(losses)
        profit_factor = (sum_wins / abs(sum_losses)) if sum_losses != 0 else (sum_wins if sum_wins > 0 else 1.0)
        
        avg_winner = (sum_wins / len(wins)) if len(wins) > 0 else 0.0
        avg_loser = (sum_losses / len(losses)) if len(losses) > 0 else 0.0
        
        # Expectancy: (WinRate * AvgWinner) - (LossRate * AvgLoser)
        loss_rate = 1.0 - (win_rate / 100.0)
        expectancy = ((win_rate / 100.0) * avg_winner) + (loss_rate * avg_loser)
        
        durations = [int(r["duration_seconds"]) for r in rows if r["duration_seconds"] is not None]
        avg_hold = sum(durations) / len(durations) if durations else 0.0
        
        # Maximum Drawdown Calculation
        cursor.execute("SELECT pnl FROM trades WHERE status='CLOSED' ORDER BY close_time ASC")
        trade_pnls = [float(t["pnl"]) for t in cursor.fetchall() if t["pnl"] is not None]
        balance_curve = [10000.0]
        current_bal = 10000.0
        max_dd = 0.0
        
        for p in trade_pnls:
            current_bal += p
            balance_curve.append(current_bal)
            
        peak = -999999.0
        for val in balance_curve:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
                
        # Group performance classifications
        sessions_pnl = {}
        tfs_pnl = {}
        setups_pnl = {}
        
        for r in rows:
            sess = r["session"] or "N/A"
            tf = r["timeframe"] or "N/A"
            setup = r["setup_name"] or "N/A"
            p = float(r["pnl"])
            
            sessions_pnl[sess] = sessions_pnl.get(sess, 0.0) + p
            tfs_pnl[tf] = tfs_pnl.get(tf, 0.0) + p
            setups_pnl[setup] = setups_pnl.get(setup, 0.0) + p
            
        best_sess = max(sessions_pnl, key=sessions_pnl.get) if sessions_pnl else "N/A"
        worst_sess = min(sessions_pnl, key=sessions_pnl.get) if sessions_pnl else "N/A"
        best_tf = max(tfs_pnl, key=tfs_pnl.get) if tfs_pnl else "N/A"
        worst_tf = min(tfs_pnl, key=tfs_pnl.get) if tfs_pnl else "N/A"
        best_setup = max(setups_pnl, key=setups_pnl.get) if setups_pnl else "N/A"
        worst_setup = min(setups_pnl, key=setups_pnl.get) if setups_pnl else "N/A"
        
        winning_conditions = []
        losing_conditions = []
        recommendations = []
        
        # Analyze parameters statistics grouped by regime
        regimes_performances = {}
        for r in rows:
            reg = r["regime"]
            pnl = float(r["pnl"])
            mfe = float(r["mfe"]) if r["mfe"] is not None else 0.0
            mae = float(r["mae"]) if r["mae"] is not None else 0.0
            
            if reg not in regimes_performances:
                regimes_performances[reg] = {"wins": 0, "losses": 0, "net_pnl": 0.0, "mfe": [], "mae": []}
            if pnl > 0:
                regimes_performances[reg]["wins"] += 1
            else:
                regimes_performances[reg]["losses"] += 1
            regimes_performances[reg]["net_pnl"] += pnl
            regimes_performances[reg]["mfe"].append(mfe)
            regimes_performances[reg]["mae"].append(mae)
            
        for reg_k, v in regimes_performances.items():
            tot = v["wins"] + v["losses"]
            wr = (v["wins"] / tot * 100) if tot > 0 else 0
            avg_mfe = np.mean(v["mfe"]) if v["mfe"] else 0
            avg_mae = np.mean(v["mae"]) if v["mae"] else 0
            
            if wr >= 60.0 and tot >= 2:
                winning_conditions.append(f"Regime {reg_k} reports a {wr:.1f}% win rate.")
                recommendations.append({
                    "type": "OPTIMIZATION",
                    "reason": f"High probability setups identified in {reg_k} regime.",
                    "suggestion": f"Confirming setups in {reg_k}: Maintain risk percentage or increase leverage profile safely."
                })
            elif wr < 45.0 and tot >= 2:
                losing_conditions.append(f"Regime {reg_k} has a low win rate ({wr:.1f}%).")
                recommendations.append({
                    "type": "TIGHTENING",
                    "reason": f"High adverse excursion in {reg_k} setups.",
                    "suggestion": f"Increase confidence_threshold in {reg_k} to 0.75 or apply a tighter trailing stop."
                })

        # Save to database parameters for telemetry persistence
        # Clean older suggestions first
        cursor.execute("DELETE FROM system_parameters WHERE name LIKE 'rec_%'")
        for idx, rec in enumerate(recommendations):
            cursor.execute(
                "INSERT OR REPLACE INTO system_parameters (name, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (f"rec_{idx}", json.dumps(rec))
            )
        conn.commit()
        
        return {
            "status": "success",
            "total_trades": total,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 2),
            "avg_winner": round(avg_winner, 2),
            "avg_loser": round(avg_loser, 2),
            "avg_hold_time_seconds": round(avg_hold, 2),
            "max_drawdown": round(max_dd, 2),
            "best_session": best_sess,
            "worst_session": worst_sess,
            "best_timeframe": best_tf,
            "worst_timeframe": worst_tf,
            "best_setup": best_setup,
            "worst_setup": worst_setup,
            "recommendations": recommendations
        }

    @staticmethod
    def perform_root_cause_analysis(pnl: float, regime: str, session: str, indicators: Dict[str, Any], mfe: float, mae: float, duration: int) -> Dict[str, Any]:
        """
        Rule 2: Root cause analysis of losing trades.
        Evaluates potential causes and assigns a confidence score (0.0 to 1.0) to each.
        """
        if pnl >= 0:
            return {}
            
        trend = indicators.get("trend", "N/A")
        rsi_val = 50.0
        try:
            momentum_str = indicators.get("momentum", "")
            if "RSI:" in momentum_str:
                rsi_val = float(momentum_str.split("RSI:")[1].split("|")[0].strip())
        except Exception:
            pass
            
        vol_str = indicators.get("volatility", "")
        vol_status = "NORMAL"
        if "HIGH" in vol_str.upper():
            vol_status = "HIGH"
        elif "LOW" in vol_str.upper():
            vol_status = "LOW"
            
        causes = {
            "wrong_trend": 0.0,
            "entered_too_early": 0.0,
            "entered_too_late": 0.0,
            "stop_loss_too_tight": 0.0,
            "stop_loss_too_wide": 0.0,
            "spread_too_high": 0.0,
            "low_liquidity": 0.0,
            "fake_breakout": 0.0,
            "weak_momentum": 0.0,
            "news_event": 0.0,
            "counter_trend_trade": 0.0,
            "wrong_market_regime": 0.0,
            "poor_risk_to_reward": 0.0,
            "volatility_spike": 0.0,
            "session_issue": 0.0
        }
        
        # 1. Wrong Trend / Counter Trend
        if trend == "BEARISH" and indicators.get("decision") == "BUY":
            causes["wrong_trend"] = 0.8
            causes["counter_trend_trade"] = 0.7
        elif trend == "BULLISH" and indicators.get("decision") == "SELL":
            causes["wrong_trend"] = 0.8
            causes["counter_trend_trade"] = 0.7
            
        # 2. Entered too early (MFE existed but reversed to hit SL)
        if mfe > 100:
            causes["entered_too_early"] = 0.6
            causes["stop_loss_too_tight"] = 0.7
            
        # 3. Entered too late (immediate MAE, no MFE)
        if mfe < 30 and mae > 100:
            causes["entered_too_late"] = 0.7
            
        # 4. Stop loss too tight
        if mae > 50 and mae < 150:
            causes["stop_loss_too_tight"] = 0.8
            
        # 5. Stop loss too wide
        if duration > 1800 and abs(pnl) > 200:
            causes["stop_loss_too_wide"] = 0.5
            
        # 6. Spread too high
        try:
            spread = int(indicators.get("spread", 0))
            if spread > 250:
                causes["spread_too_high"] = 0.9
        except Exception:
            pass
            
        # 7. Low liquidity
        if session == "Asian Session" or vol_status == "LOW":
            causes["low_liquidity"] = 0.6
            
        # 8. Fake breakout
        structure = indicators.get("structure", "")
        if "BREAK" in structure or "BOS" in structure:
            if mfe < 55:
                causes["fake_breakout"] = 0.8
                
        # 9. Weak momentum
        if abs(rsi_val - 50.0) < 5.0:
            causes["weak_momentum"] = 0.7
            
        # 10. Wrong market regime
        if "RANGE" in regime.upper() or "COMPRESSION" in regime.upper():
            causes["wrong_market_regime"] = 0.7
            
        # 11. Volatility spike
        if vol_status == "HIGH":
            causes["volatility_spike"] = 0.7
            
        sorted_causes = sorted(causes.items(), key=lambda x: x[1], reverse=True)
        primary_cause = sorted_causes[0][0] if sorted_causes[0][1] > 0.3 else "market_noise"
        
        return {
            "primary_cause": primary_cause.replace('_', ' ').title(),
            "confidence_scores": causes,
            "analyzed_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        }

    @staticmethod
    def perform_winning_trade_analysis(pnl: float, regime: str, session: str, indicators: Dict[str, Any]) -> Dict[str, Any]:
        """
        Rule 3: Winning trade setup profiling.
        """
        if pnl <= 0:
            return {}
            
        trend = indicators.get("trend", "N/A")
        structure = indicators.get("structure", "N/A")
        liquidity = indicators.get("liquidity", "N/A")
        entry_score = indicators.get("score", 0)
        
        confirmations = []
        if trend != "N/A" and trend != "NEUTRAL":
            confirmations.append("trend_aligned")
        if "BOS" in structure or "CHOCH" in structure or "BREAK" in structure:
            confirmations.append("structure_breakout")
        if "SWEEP" in liquidity or "POOL" in liquidity:
            confirmations.append("liquidity_sweep")
        if entry_score >= 80:
            confirmations.append("high_confluence")
            
        return {
            "success_reason": "Setup confluence confirmation met targets",
            "confirmations": confirmations,
            "regime": regime,
            "session": session,
            "trend": trend,
            "entry_score": entry_score,
            "analyzed_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        }

    @staticmethod
    def search_similar_setups(conn, symbol: str, regime: str, session: str, direction: str) -> Dict[str, Any]:
        """
        Rule 4: Similar Setup Search
        Queries past learning outcomes with similar setups.
        """
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT pnl, mae, duration_seconds 
            FROM learning_outcomes 
            WHERE regime = ? AND session = ? AND ticket IN (
                SELECT ticket FROM trades WHERE direction = ?
            )
            """,
            (regime, session, direction)
        )
        rows = cursor.fetchall()
        
        if not rows:
            return {
                "count": 0,
                "win_rate": 100.0,
                "avg_profit": 0.0,
                "avg_drawdown_pts": 0.0,
                "avg_hold_time_mins": 0.0,
                "actionable": True
            }
            
        count = len(rows)
        wins = [float(r["pnl"]) for r in rows if float(r["pnl"]) > 0]
        win_rate = (len(wins) / count) * 100.0
        
        avg_profit = sum([float(r["pnl"]) for r in rows]) / count
        avg_drawdown = sum([float(r["mae"]) for r in rows if r["mae"] is not None]) / count
        avg_hold = (sum([int(r["duration_seconds"]) for r in rows if r["duration_seconds"] is not None]) / count) / 60.0
        
        actionable = True
        if count >= 3 and win_rate < 50.0:
            actionable = False
            
        return {
            "count": count,
            "win_rate": round(win_rate, 2),
            "avg_profit": round(avg_profit, 2),
            "avg_drawdown_pts": round(avg_drawdown, 2),
            "avg_hold_time_mins": round(avg_hold, 1),
            "actionable": actionable
        }

    @staticmethod
    def generate_trade_screenshot(ticket: int, stage: str, symbol: str, price: float, direction: str) -> str:
        """
        Rule 1: Screenshot before entry / exit. Generates a beautiful champagne-gold mockup chart.
        """
        import os
        from Titan.config.config import BASE_DIR
        
        screenshot_dir = os.path.join(BASE_DIR, "Titan", "dashboard", "static", "screenshots")
        if not os.path.exists(screenshot_dir):
            try:
                os.makedirs(screenshot_dir)
            except Exception:
                pass
            
        file_name = f"{ticket}_{stage}.png"
        full_path = os.path.join(screenshot_dir, file_name)
        
        try:
            from PIL import Image, ImageDraw
            img = Image.new('RGB', (600, 350), color='#12141a')
            draw = ImageDraw.Draw(img)
            draw.rectangle([(5, 5), (595, 345)], outline='#d4af37', width=2)
            draw.text((20, 20), "TITAN V8 COGNITIVE INTELLIGENCE ENGINE", fill='#d4af37')
            draw.text((20, 45), f"TRADE EXECUTION CHART RECORD - ID #{ticket}", fill='#ffffff')
            draw.text((20, 90), f"Stage: {stage}", fill='#c5a880')
            draw.text((20, 115), f"Symbol: {symbol}", fill='#ffffff')
            draw.text((20, 140), f"Direction: {direction}", fill='#00e676' if direction == 'BUY' else '#ff1744')
            draw.text((20, 165), f"Reference Price: {price:.3f} USD", fill='#ffffff')
            draw.text((20, 190), f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC", fill='#90a4ae')
            draw.line([(50, 270), (200, 250), (350, 290), (500, 230), (550, 240)], fill='#d4af37', width=2)
            draw.ellipse([(500 - 5, 230 - 5), (500 + 5, 230 + 5)], fill='#00e676' if stage == "ENTRY" else '#ff5252')
            draw.text((490, 205), stage, fill='#ffffff')
            img.save(full_path)
            return f"/static/screenshots/{file_name}"
        except Exception:
            txt_path = full_path.replace(".png", ".txt")
            try:
                with open(txt_path, "w") as f:
                    f.write(f"TITAN V8 CHART EXPORT #{ticket} [{stage}]\nSymbol: {symbol}\nDirection: {direction}\nPrice: {price}\n")
            except Exception:
                pass
            return f"/static/screenshots/{ticket}_{stage}.txt"
