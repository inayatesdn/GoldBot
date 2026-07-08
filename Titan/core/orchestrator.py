import time
import json
import threading
from typing import Dict, Any
from datetime import datetime, timezone
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, get_settings
from Titan.storage.db import get_db_connection
from Titan.core.state import state
from Titan.core.logger import system_logger, trading_logger
from Titan.execution.mt5_client import MT5Client
from Titan.services.market_data_service import MarketDataService
from Titan.services.decision_engine import DecisionEngine
from Titan.services.risk_engine import RiskEngine
from Titan.services.execution_engine import ExecutionEngine
from Titan.services.position_manager import PositionManager
from Titan.services.basket_manager import BasketManager
from Titan.services.learning_engine import LearningEngine
from Titan.services.websocket_service import websocket_service

class AutonomousDaemon:
    def __init__(self, symbol: str = PRIMARY_SYMBOL):
        self.symbol = symbol
        self.logger = system_logger
        self.is_running = False
        
        # Instance our modular services
        self.market_data = MarketDataService(self.symbol)
        self.execution = ExecutionEngine(self.symbol)
        self.decision = DecisionEngine(self.symbol)
        self.risk = RiskEngine(self.symbol)
        
        self.position_manager = PositionManager(self.execution, self.symbol)
        self.basket_manager = BasketManager(self.execution, self.symbol)
        self.learning = LearningEngine()
        
        self.thread = None
        self.last_db_log_time = 0
        self.last_learning_sync_time = 0
        self.last_hourly_eval_time = 0

    def start(self):
        """Starts the autonomous trading loop in a background daemon thread."""
        if self.is_running:
            self.logger.warning("AutonomousDaemon is already running.")
            return
            
        self.is_running = True
        self.market_data.start()
        
        self.thread = threading.Thread(target=self.run_loop, name="TitanAutonomousLoop", daemon=True)
        self.thread.start()
        self.logger.info("AutonomousDaemon started background execution thread.")

    def stop(self):
        """Stops the autonomous loop execution."""
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=3.0)
        self.logger.info("AutonomousDaemon execution thread stopped.")

    def halt(self):
        """Emergency Halt: Liquidates all open positions and suspends autonomous cycles."""
        state.lock.acquire()
        state.emergency_halt = True
        state.lock.release()
        self.logger.warning("EMERGENCY HALT REQUEST RECEIVED. Suspending trading flow...")
        
        # Save change to database settings
        settings = get_settings()
        settings["emergency_halt"] = True
        settings["auto_trade"] = False
        from Titan.config.config import save_settings
        save_settings(settings)
        
        # Execute immediate positions closure
        self.execution.close_all_positions()

    def resume(self):
        """Resumes active auto trade status."""
        state.lock.acquire()
        state.emergency_halt = False
        state.lock.release()
        self.logger.info("EMERGENCY HALT DISENGAGED. Resuming quantitative loop.")
        
        # Save to database
        settings = get_settings()
        settings["emergency_halt"] = False
        settings["auto_trade"] = True
        from Titan.config.config import save_settings
        save_settings(settings)

    def run_loop(self):
        """The permanent core execution loop running every 100 milliseconds."""
        self.logger.info("Starting permanent execution loop...")
        
        while self.is_running:
            cycle_start = time.time()
            try:
                # 1. MT5 Reconnection Management
                connected = self.market_data.reconnect_if_needed()
                
                # Fetch settings
                settings = get_settings()
                
                # Sync flags with global state
                state.lock.acquire()
                state.auto_trade = settings.get("auto_trade", False)
                state.emergency_halt = settings.get("emergency_halt", False)
                if state.emergency_halt:
                    state.strategy_status = "EMERGENCY HALTED"
                elif state.auto_trade:
                    state.strategy_status = "AUTOTRADE ACTIVE"
                else:
                    state.strategy_status = "MONITORING ONLY (OFF)"
                state.lock.release()

                # Sync account stats if online
                if connected:
                    acct = mt5.account_info()
                    if acct is not None:
                        state.lock.acquire()
                        state.account = str(acct.login)
                        state.name = acct.name
                        state.server = acct.server
                        state.balance = acct.balance
                        state.equity = acct.equity
                        state.margin = acct.margin
                        state.margin_free = acct.margin_free
                        state.margin_level = acct.margin_level if acct.margin > 0 else 0.0
                        state.profit = acct.profit
                        state.currency = acct.currency
                        state.lock.release()

                    # 2. Get latest tick
                    tick_info = self.market_data.update_tick()
                    
                    if tick_info:
                        # 3. Update market state indicators and calculate signal confluences
                        decision_res = self.decision.evaluate_signals()
                        
                        # 4. Manage open positions (Trailing, Breakevens, Partials)
                        self.position_manager.adjust_open_positions()
                        
                        # 5. Manage baskets (Basket TP/SL targets, lot exposure guards)
                        self.basket_manager.monitor_and_manage_basket()
                        
                        # 6. Database log decision records on signals or at 60-second intervals
                        self.log_decision_to_database(decision_res)
                        
                        # 7. Check trade triggers if auto trade active
                        if decision_res["decision"] in ["BUY", "SELL"] and state.auto_trade and not state.emergency_halt:
                            # Rule 6 Secondary Entry validations
                            state.lock.acquire()
                            open_positions = list(state.open_positions)
                            state.lock.release()
                            
                            is_secondary_entry = len(open_positions) > 0
                            allow_entry = True
                            
                            if is_secondary_entry:
                                # Find existing positions of same direction
                                matching = [p for p in open_positions if p["symbol"] == self.symbol and p["type"] == decision_res["decision"]]
                                if matching:
                                    # Sort oldest first to get initial entry price
                                    matching.sort(key=lambda x: x["time"])
                                    primary_entry = matching[0]["price_open"]
                                    
                                    # Get current price
                                    curr_price = tick_info["bid"] if decision_res["decision"] == "SELL" else tick_info["ask"]
                                    
                                    # Get current ATR from decision_res
                                    atr_str = decision_res.get("volatility", "")
                                    atr_val = 1.0
                                    try:
                                        if "ATR:" in atr_str:
                                            atr_val = float(atr_str.split("ATR:")[1].split("(")[0].strip())
                                    except Exception:
                                        pass
                                        
                                    price_diff = curr_price - primary_entry
                                    required_dist = 3.0 * atr_val
                                    
                                    is_better = False
                                    if decision_res["decision"] == "BUY" and price_diff <= -required_dist:
                                        is_better = True
                                    elif decision_res["decision"] == "SELL" and price_diff >= required_dist:
                                        is_better = True
                                    
                                    if not is_better:
                                        self.logger.info(
                                            f"[Rule 6 Secondary Entry Blocked] Current price ({curr_price}) not 3 ATR points better than "
                                            f"primary entry ({primary_entry}). Req distance: {required_dist:.3f} USD. Diff: {price_diff:.3f} USD."
                                        )
                                        allow_entry = False
                                        
                                    # Exposure limit check
                                    max_exp = float(settings.get("max_exposure_lots", 5.0))
                                    total_vol = sum([p["volume"] for p in open_positions])
                                    if total_vol >= max_exp:
                                        self.logger.warning(
                                            f"[Rule 6 Exposure Lock] Total active basket volume {total_vol:.2f} >= Limit {max_exp:.2f}. Blocking secondary entry."
                                        )
                                        allow_entry = False
                                        
                            if allow_entry:
                                buy_s = decision_res.get('buy_score', 0)
                                sell_s = decision_res.get('sell_score', 0)
                                trend = decision_res.get('trend', 'UNKNOWN')
                                chosen_dir = decision_res["decision"]
                                
                                # Print execution verification (Step 2)
                                verification_str = (
                                    f"\n======== EXECUTION VERIFICATION ========\n"
                                    f"Trend             : {trend}\n"
                                    f"BUY Score         : {buy_s}\n"
                                    f"SELL Score        : {sell_s}\n"
                                    f"Chosen Direction  : {chosen_dir}\n"
                                    f"Reason            : {decision_res.get('reason', '')}\n"
                                    f"========================================"
                                )
                                self.logger.info(verification_str)
                                
                                # Safety direction audit
                                execute_valid = True
                                if chosen_dir == "BUY" and sell_s > buy_s:
                                    self.logger.error("TRADE CANCELED: Chosen direction is BUY but SELL Score is higher.")
                                    execute_valid = False
                                elif chosen_dir == "SELL" and buy_s > sell_s:
                                    self.logger.error("TRADE CANCELED: Chosen direction is SELL but BUY Score is higher.")
                                    execute_valid = False
                                
                                if execute_valid:
                                    qualification = self.risk.qualify_new_order(chosen_dir, tick_info)
                                    if qualification["qualified"]:
                                        self.logger.info(f"Signal qualified. Submitting execution order request: {chosen_dir}")
                                    res_order = self.execution.execute_order(
                                        action=decision_res["decision"],
                                        volume=qualification["lot_size"],
                                        sl=qualification["sl"],
                                        tp=qualification["tp"],
                                        comment="Titan Autonomous Entry"
                                    )
                                    if res_order and res_order.get("success", False):
                                        new_tkt = res_order["ticket"]
                                        curr_p = res_order["price"]
                                        from Titan.learning.learning_engine import LearningEngine as CoreLearningEngine
                                        scr_path = CoreLearningEngine.generate_trade_screenshot(new_tkt, "ENTRY", self.symbol, curr_p, decision_res["decision"])
                                        state.lock.acquire()
                                        state.screenshot_entry_map[new_tkt] = scr_path
                                        state.lock.release()
                                
                    # 8. Learning outcomes synchronization (runs every 10 seconds to save performance)
                    now_sec = time.time()
                    if now_sec - self.last_learning_sync_time >= 10.0:
                        self.learning.sync_outcomes(MT5Client)
                        self.last_learning_sync_time = now_sec
                        
                    # Hourly performance evaluation (Rule 8)
                    if now_sec - self.last_hourly_eval_time >= 3600.0:
                        self.last_hourly_eval_time = now_sec
                        self.logger.info("Executing hourly strategy performance evaluation and recommendation compile...")
                        self.learning.get_recommendations()
                        
                else:
                    self.logger.warning("Waiting for MT5 terminal connection...")

                # 9. Queue updates to WebSocket server
                self.push_websocket_updates()

            except Exception as e:
                self.logger.error(f"Error encountered during autonomous loop cycle: {e}", exc_info=True)

            # Sleep remaining time to sustain exactly 100ms cycles
            elapsed = time.time() - cycle_start
            sleep_time = max(0.005, 0.100 - elapsed)
            time.sleep(sleep_time)

    def log_decision_to_database(self, dec: Dict[str, Any]):
        """Logs calculated decisions to SQLite target tables regularly or on active signals."""
        now = time.time()
        is_signal = dec["decision"] in ["BUY", "SELL"]
        is_minute_passed = (now - self.last_db_log_time >= 60.0)
        
        if is_signal or is_minute_passed:
            self.last_db_log_time = now
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                evidence = {
                    "regime": dec.get("regime", "N/A"),
                    "trend": dec.get("trend", "N/A"),
                    "momentum": dec.get("momentum", "N/A"),
                    "volatility": dec.get("volatility", "N/A")
                }
                cursor.execute(
                    """
                    INSERT INTO decisions (symbol, decision, score, confidence, reason, evidence_json, timeframe)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (self.symbol, dec["decision"], dec["score"], dec["confidence"], dec["reason"], json.dumps(evidence), "M1")
                )
                conn.commit()
            except Exception as e:
                self.logger.error(f"Failed to log decision to database: {e}")
            finally:
                conn.close()

    def push_websocket_updates(self):
        """Pushes telemetry variables, decisions, active list, and tick changes over WebSockets."""
        state.lock.acquire()
        telemetry = state.to_telemetry_dict()
        positions = list(state.open_positions)
        decision = dict(state.latest_decision)
        candles = list(state.candles)
        state.lock.release()
        
        # Broadcast separate payloads
        websocket_service.push_update("telemetry", telemetry)
        websocket_service.push_update("positions", positions)
        websocket_service.push_update("decision", decision)
        
        if candles:
            # Send only the current (last) candle update for high frequency chart ticking
            # LightweightCharts update() takes a single bar object
            websocket_service.push_update("candle_update", candles[-1])
