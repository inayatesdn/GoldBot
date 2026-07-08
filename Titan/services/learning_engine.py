import logging
from typing import Dict, Any
from Titan.learning.learning_engine import LearningEngine as CoreLearningEngine
from Titan.storage.db import get_db_connection
from Titan.core.state import state
from Titan.core.logger import learning_logger

class LearningEngine:
    def __init__(self):
        self.logger = learning_logger

    def sync_outcomes(self, mt5_client: Any):
        """Fetches newly completed trades, computes MAE/MFE, and logs results in history."""
        self.logger.info("Synchronizing completed trade outcome diagnostics...")
        conn = get_db_connection()
        try:
            CoreLearningEngine.process_completed_trades(conn, mt5_client)
            
            # Recalculate stats for dashboard
            stats = CoreLearningEngine.analyze_performance(conn)
            
            # Query actual closed P/L for today
            from datetime import datetime, timezone
            today_start_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            cursor = conn.cursor()
            cursor.execute(
                "SELECT SUM(pnl) as today_pnl FROM trades WHERE status='CLOSED' AND close_time >= ?",
                (today_start_date + " 00:00:00",)
            )
            row = cursor.fetchone()
            today_pnl = float(row["today_pnl"]) if row and row["today_pnl"] is not None else 0.0
            
            state.lock.acquire()
            if stats and stats.get("status") == "success":
                state.win_rate = stats.get("win_rate", 0.0)
            state.today_closed_pnl = today_pnl
            state.lock.release()
                
        except Exception as e:
            self.logger.error(f"Error during completed trade synchronization: {e}")
        finally:
            conn.close()

    def get_recommendations(self) -> Dict[str, Any]:
        """Runs performance statistics computation manually to offer parameters recommendations."""
        conn = get_db_connection()
        try:
            return CoreLearningEngine.analyze_performance(conn)
        except Exception as e:
            self.logger.error(f"Error compiling recommendations: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            conn.close()
