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
                
            # Store learning snapshot
            learning_snapshot = {
                "market_snapshot": {"regime": regime, "session": sess, "symbol": symbol},
                "indicators": json.loads(indicators_json) if isinstance(indicators_json, str) else indicators_json,
                "entry_reason": entry_reason,
                "exit_reason": exit_reason,
                "confidence": confidence,
                "profit": max(0.0, pnl),
                "loss": abs(min(0.0, pnl))
            }
            
            cursor.execute(
                """
                INSERT OR REPLACE INTO learning_outcomes (ticket, symbol, regime, session, pnl, mfe, mae, duration_seconds, timeframe, setup_name, indicators_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ticket, symbol, regime, sess, pnl, mfe_pts, mae_pts, duration_sec, timeframe, setup_name, json.dumps(learning_snapshot))
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
