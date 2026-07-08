import time
import logging
import json
from datetime import datetime, timezone
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, DB_PATH, get_settings
from Titan.storage.db import get_db_connection
from Titan.execution.mt5_client import MT5Client
from Titan.market.regime import RegimeClassifier
from Titan.market.scanner import MultiTimeframeScanner
from Titan.market.sessions import SessionManager
from Titan.market.economic_calendar import EconomicCalendar
from Titan.strategies.technical_analysis import TechAnalysis
from Titan.market.intelligence.regime import MarketRegimeEngine
from Titan.market.intelligence.structure import StructureEngine
from Titan.market.intelligence.liquidity import LiquidityEngine
from Titan.market.intelligence.smc import SmartMoneyEngine
from Titan.market.intelligence.session import SessionEngine
from Titan.market.intelligence.volume import VolumeEngine
from Titan.market.intelligence.momentum import MomentumEngine
from Titan.market.intelligence.mtf import MultiTimeframeEngine
from Titan.market.intelligence.confluence import ConfluenceEngine
from Titan.market.intelligence.decision import DecisionEngine as NewDecisionEngine
from Titan.core.smart_entry import SmartEntryEngine
from Titan.risk.qualification import QualificationEngine
from Titan.execution.position_manager import PositionManager
from Titan.learning.learning_engine import LearningEngine

# Setup Logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler("Titan/logs/daemon.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Titan.Orchestrator")

def make_json_serializable(val):
    if isinstance(val, dict):
        return {k: make_json_serializable(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [make_json_serializable(v) for v in val]
    elif hasattr(val, "item"):
        return val.item()
    return val

class AutonomousDaemon:
    def __init__(self):
        self.is_running = False
        self.emergency_halt = False
        self.last_candle_run_time = 0
        self.loop_interval_sec = 2.0  # Open positions are updated every 2 seconds

    def halt(self):
        self.emergency_halt = True
        self.is_running = False
        logger.warning("EMERGENCY STOP ENGAGED! Stopping orchestrator loop...")
        
        # Emergency close all open positions
        conn = get_db_connection()
        try:
            open_positions = MT5Client.get_open_positions()
            for pos in open_positions:
                logger.warning(f"Emergency closing position ticket {pos['ticket']}")
                MT5Client.close_position(pos["ticket"], comment="Titan Emergency Close")
        except Exception as e:
            logger.error(f"Failed to execute emergency closure: {e}")
        finally:
            conn.close()

    def resume(self):
        self.emergency_halt = False
        self.is_running = True
        logger.info("Emergency halt disengaged. Resuming operations.")

    def run_cycle(self):
        """
        Executes one loop cycle:
        1. Sync telemetry from MT5
        2. Closed trades analytics logging
        3. Open position active trailing and breakeven adjustments
        4. High-conviction multi-timeframe strategy queries on candle close
        5. Execution and order submission
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # 1. Connection check
            if not MT5Client.check_connection():
                logger.error("MetaTrader 5 connection unavailable. Skipping cycle.")
                return
                
            # 2. Position Management (breakeven & partial profit adjustments)
            PositionManager.adjust_open_positions(conn, MT5Client)
            
            # 3. Learning Ingestion (closed trades outcomes indexing)
            LearningEngine.process_completed_trades(conn, MT5Client)
            
            # Fetch settings statically to determine execution timeframe
            settings = get_settings()
            tf_config = settings.get("timeframes", {})
            exec_tf_str = tf_config.get("execution", "M1")
            
            # Translate to MT5 timeframe values
            TF_MAP = {
                "M1": mt5.TIMEFRAME_M1,
                "M3": mt5.TIMEFRAME_M3,
                "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15,
                "M30": mt5.TIMEFRAME_M30,
                "H1": mt5.TIMEFRAME_H1
            }
            exec_tf = TF_MAP.get(exec_tf_str, mt5.TIMEFRAME_M1)
            
            # Check for candle close on the configured execution timeframe
            rates_raw = mt5.copy_rates_from_pos(PRIMARY_SYMBOL, exec_tf, 0, 2)
            if rates_raw is None or len(rates_raw) < 2:
                return
                
            last_finished_candle_time = int(rates_raw[0][0])
            
            # Execute trade decision logic only once per candle close
            if last_finished_candle_time > self.last_candle_run_time:
                logger.info(f"New {exec_tf_str} execution candle detected ({datetime.fromtimestamp(last_finished_candle_time, timezone.utc).strftime('%H:%M')} UTC). Running Market Intelligence Engines...")
                
                def parse_rates(rates):
                    out = []
                    for r in rates:
                        out.append({
                            "time": int(r[0]), 
                            "open": float(r[1]), 
                            "high": float(r[2]), 
                            "low": float(r[3]), 
                            "close": float(r[4]),
                            "tick_volume": int(r[5])
                        })
                    return out
                
                def get_rates_safe(symbol, timeframe, count):
                    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
                    if rates is None or len(rates) == 0:
                        return []
                    return parse_rates(rates)
                    
                # Fetch candles for MTF alignment
                all_timeframes_data = {
                    "M1": get_rates_safe(PRIMARY_SYMBOL, mt5.TIMEFRAME_M1, 100),
                    "M3": get_rates_safe(PRIMARY_SYMBOL, mt5.TIMEFRAME_M3, 100),
                    "M5": get_rates_safe(PRIMARY_SYMBOL, mt5.TIMEFRAME_M5, 100),
                    "M15": get_rates_safe(PRIMARY_SYMBOL, mt5.TIMEFRAME_M15, 100),
                    "M30": get_rates_safe(PRIMARY_SYMBOL, mt5.TIMEFRAME_M30, 100),
                    "H1": get_rates_safe(PRIMARY_SYMBOL, mt5.TIMEFRAME_H1, 100)
                }
                
                exec_list = all_timeframes_data.get(exec_tf_str, all_timeframes_data["M1"])
                if len(exec_list) < 20:
                    logger.error(f"Failed to collect basic {exec_tf_str} rates from MT5 terminal.")
                    return
                    
                # A. Run independent market intelligence calculations on dynamic execution timeframe
                regime_res = MarketRegimeEngine.classify(exec_list)
                struct_res = StructureEngine.analyze(exec_list)
                liq_res = LiquidityEngine.analyze(exec_list)
                mom_res = MomentumEngine.analyze(exec_list)
                vol_res = VolumeEngine.analyze(exec_list)
                sess_res = SessionEngine.analyze(exec_list)
                smc_res = SmartMoneyEngine.analyze(exec_list)
                
                # B. Run Multi-Timeframe Alignment
                mtf_res = MultiTimeframeEngine.analyze(all_timeframes_data)
                
                # C. Retrieve live tick details
                live_tick = MT5Client.get_live_tick(PRIMARY_SYMBOL)
                if not live_tick:
                    return
                    
                symbol_inf = mt5.symbol_info(PRIMARY_SYMBOL)
                point = symbol_inf.point if symbol_inf else 0.01
                spread_pts = round(live_tick["spread"] / point) if point > 0 else 10
                
                # D. Check News lock
                is_news_locked, mins_left, event_title = EconomicCalendar.check_news_lock()
                
                # E. Calculate Confluence Out of 100
                confluence_res = ConfluenceEngine.calculate(
                    regime_res, struct_res, liq_res, mom_res, vol_res, sess_res, is_news_locked
                )
                
                # F. Run Decision Engine
                exec_atr = regime_res["metrics"].get("atr_14", 1.0)
                decision_res = NewDecisionEngine.evaluate(
                    confluence_res=confluence_res,
                    regime_res=regime_res,
                    struct_res=struct_res,
                    liq_res=liq_res,
                    mom_res=mom_res,
                    vol_res=vol_res,
                    sess_res=sess_res,
                    smc_res=smc_res,
                    spread_pts=spread_pts,
                    is_news_locked=is_news_locked,
                    settings=settings,
                    point_size=point,
                    m1_atr=exec_atr,
                    last_close=live_tick["ask"],
                    mtf_res=mtf_res
                )
                
                # G. Check Dynamic Trend Alignment
                mtf_state = mtf_res["state"]
                target_decision = decision_res["decision"]
                trend_aligned = False
                
                if target_decision == "BUY" and mtf_state == "BULLISH_ALIGNMENT":
                    trend_aligned = True
                elif target_decision == "SELL" and mtf_state == "BEARISH_ALIGNMENT":
                    trend_aligned = True
                elif mtf_state == "NEUTRAL":
                    # For quick setups (M1/M3) run if local TFs (Exec and M3) align with decision
                    exec_bias = "BUY" if "BULL" in regime_res.get("reason", "").upper() or "BULL" in regime_res.get("state", "").upper() else "SELL"
                    m3_bias = "BUY" if "BULL" in mtf_res["metrics"]["timeframes"].get("M3", {}).get("regime", {}).get("reason", "").upper() or "BULL" in mtf_res["metrics"]["timeframes"].get("M3", {}).get("regime", {}).get("state", "").upper() else "SELL"
                    if target_decision == "BUY" and exec_bias == "BUY" and m3_bias == "BUY":
                        trend_aligned = True
                    elif target_decision == "SELL" and exec_bias == "SELL" and m3_bias == "SELL":
                        trend_aligned = True
                
                confluences = {
                    "trend_aligned": trend_aligned,
                    "macro_trend": regime_res["state"],
                    "m1_metrics": {
                        "date": datetime.fromtimestamp(exec_list[-1]["time"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        "close": live_tick["bid"],
                        "atr_14": regime_res["metrics"]["atr_14"],
                        "rsi": mom_res["metrics"]["rsi"],
                        "macd_bullish": float(mom_res["metrics"]["macd"]["histogram"]) > 0,
                        "macd_bearish": float(mom_res["metrics"]["macd"]["histogram"]) < 0,
                        "bos": struct_res["metrics"]["bos_bullish"] or struct_res["metrics"]["bos_bearish"],
                        "choch": struct_res["metrics"]["choch_bullish"] or struct_res["metrics"]["choch_bearish"],
                        "ob_touched": smc_res["state"] in ["BULLISH_OB_RETEST", "BEARISH_OB_RETEST"],
                        "fvg_touched": smc_res["state"] in ["BULLISH_FVG_FILL", "BEARISH_FVG_FILL"]
                    },
                    "m3_metrics": {
                        "bos": False, "choch": False, "ob_touched": False, "fvg_touched": False
                    },
                    "m5_metrics": {
                        "adx": regime_res["metrics"]["adx_14"]
                    }
                }
                
                # Save decision history to database
                cursor.execute(
                    """
                    INSERT INTO decisions (symbol, decision, score, confidence, reason, evidence_json, timeframe)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (PRIMARY_SYMBOL, decision_res["decision"], decision_res["score"], 
                     decision_res["confidence"], decision_res["reason"], 
                     json.dumps(make_json_serializable({
                         "regime": regime_res["state"],
                         "confluences": confluences, 
                         "news_locked": is_news_locked,
                         "spread_points": spread_pts,
                         "breakdown": confluence_res["metrics"]
                     })), f"{exec_tf_str}_MTF")
                )
                conn.commit()
                
                logger.info(f"MTF Decision ({exec_tf_str}): {decision_res['decision']} (Score: {decision_res['score']}, Confidence: {decision_res['confidence']})")
                
                # H. Qualification Checks
                if target_decision in ["BUY", "SELL"]:
                    auto_trade_on = settings.get("auto_trade", False)
                    if not auto_trade_on:
                        logger.warning(f"Decision was {target_decision} but AUTO_TRADE is off in command center settings profile.")
                    else:
                        qualify_res = QualificationEngine.qualify_trade(
                            PRIMARY_SYMBOL, target_decision, MT5Client, conn, live_tick, exec_list, confluences, decision_res
                        )
                        
                        if qualify_res["qualified"]:
                            # Execute orders via MT5 client SDK
                            exec_res = MT5Client.execute_order(
                                PRIMARY_SYMBOL, target_decision, qualify_res["lot_size"],
                                qualify_res["sl"], qualify_res["tp"], f"Titan V2 {exec_tf_str} Execution"
                            )
                            
                            if exec_res["success"]:
                                ticket = exec_res["ticket"]
                                cursor.execute(
                                    """
                                    INSERT INTO trades (ticket, symbol, direction, volume, entry_price, sl, tp, open_time, status, regime_at_opening)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'EXECUTED', ?)
                                    """,
                                    (ticket, PRIMARY_SYMBOL, target_decision, qualify_res["lot_size"],
                                     exec_res["price"], qualify_res['sl'], qualify_res['tp'], confluences["macro_trend"])
                                )
                                conn.commit()
                                logger.info(f"Order executed successfully on broker: ticket={ticket}, lots={qualify_res['lot_size']}")
                            else:
                                logger.error(f"Broker order submission failed: {exec_res.get('error')}")
                        else:
                            logger.info(f"Trade qualification rejected: {qualify_res['reason']}")
                            
                self.last_candle_run_time = last_finished_candle_time
                
        except Exception as ex:
            logger.exception(f"Unexpected error in orchestrator daemon loop: {ex}")
        finally:
            conn.close()

    def start_loop(self):
        if self.is_running:
            return
            
        logger.info("Initializing Titan V2 Autonomous Daemon Orchestrator...")
        if not MT5Client.initialize():
            logger.error("Initialization failed. Cannot start daemon.")
            return
            
        self.is_running = True
        self.emergency_halt = False
        
        while self.is_running:
            if self.emergency_halt:
                time.sleep(1.0)
                continue
                
            self.run_cycle()
            time.sleep(self.loop_interval_sec)
            
        MT5Client.shutdown()
        logger.info("Orchestrator daemon loop stopped.")
