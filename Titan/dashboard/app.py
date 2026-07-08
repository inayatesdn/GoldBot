import os
import threading
import logging
import json
import asyncio
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import time
from datetime import datetime, timezone
import MetaTrader5 as mt5

from Titan.config.config import BASE_DIR, PRIMARY_SYMBOL, get_settings, save_settings
from Titan.storage.db import get_db_connection
from Titan.core.state import state
from Titan.core.orchestrator import AutonomousDaemon
from Titan.services.websocket_service import websocket_service
from Titan.research.backtester import Backtester

# Initialize FastAPI App
app = FastAPI(title="TITAN V2 - Institutional Command Center")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup Logger
logger = logging.getLogger("Titan.DashboardAPI")

# Global Daemon
daemon_instance = AutonomousDaemon()

class BacktestRequest(BaseModel):
    symbol: str = "XAUUSD"
    preset_range: str = "last_week"

class ManualTradeRequest(BaseModel):
    action: str
    volume: float

class SettingsModel(BaseModel):
    risk_pct: float
    max_daily_loss: float
    max_concurrent_positions: int
    trading_session: str
    confidence_threshold: float
    atr_multiplier: float
    tp_multiplier: float
    news_lock: bool
    spread_limit: int
    slippage_limit: int
    auto_trade: bool

@app.on_event("startup")
def startup_event():
    # Launch autonomous background process
    daemon_instance.start()
    
    # Spawn WebSocket broadcast task in the main asyncio event loop
    loop = asyncio.get_event_loop()
    loop.create_task(websocket_service.run_broadcast_worker())
    logger.info("Titan Autonomous Daemon and WebSocket routing worker triggered.")

@app.on_event("shutdown")
def shutdown_event():
    daemon_instance.stop()
    mt5.shutdown()
    logger.info("Titan Terminal links released.")

@app.websocket("/ws")
async def websocket_route(websocket: WebSocket):
    """Establishes real-time push telemetry pipe to index.html UI."""
    await websocket_service.connect(websocket)
    try:
        while True:
            # Maintain pipe open and listen for close signals
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_service.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket client error: {e}")
        websocket_service.disconnect(websocket)

@app.get("/api/telemetry")
def get_telemetry():
    """Returns MT5 connection and account health statistics from thread-safe state cache."""
    return state.to_telemetry_dict()

@app.get("/api/positions")
def get_positions():
    """Returns lists of open positions cached in thread-safe state, enriched with analytics."""
    state.lock.acquire()
    positions = list(state.open_positions)
    state.lock.release()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    enriched = []
    for pos in positions:
        ticket = pos["ticket"]
        symbol = pos["symbol"]
        volume = pos["volume"]
        direction = pos["type"] # BUY/SELL
        entry = pos["price_open"]
        curr = pos["price_current"]
        sl = pos["sl"]
        tp = pos["tp"]
        
        mult = 100.0 if "XAU" in symbol or "GOLD" in symbol else 100000.0
        risk_val = abs(entry - sl) * volume * mult if sl > 0 else 0.0
        reward_val = abs(entry - tp) * volume * mult if tp > 0 else 0.0
        
        be_active = False
        trailing_active = False
        if direction == "BUY":
            be_active = (sl >= entry - 0.01)
            trailing_active = (sl > entry + 0.10)
        else: # SELL
            be_active = (sl <= entry + 0.01)
            trailing_active = (sl < entry - 0.10)
            
        # Try database lookups for metadata
        cursor.execute("SELECT strategy_name, confidence_at_entry, open_time FROM trades WHERE ticket = ?", (ticket,))
        tr_row = cursor.fetchone()
        
        strategy_name = tr_row["strategy_name"] if tr_row else "Titan Scalper"
        confidence = tr_row["confidence_at_entry"] if tr_row else 0.70
        open_time_str = tr_row["open_time"] if tr_row else datetime.fromtimestamp(pos["time"], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        # Load latest rationale
        cursor.execute("SELECT reason FROM decisions WHERE timestamp <= ? ORDER BY id DESC LIMIT 1", (open_time_str,))
        dec_row = cursor.fetchone()
        ai_explanation = dec_row["reason"] if dec_row else "Titan automated confluences."
        
        enriched.append({
            "ticket": ticket,
            "symbol": symbol,
            "type": direction,
            "volume": volume,
            "price_open": entry,
            "price_current": curr,
            "sl": sl,
            "tp": tp,
            "profit": pos["profit"],
            "swap": pos["swap"],
            "commission": pos["commission"],
            "magic": pos["magic"],
            "strategy_name": strategy_name,
            "confidence": confidence,
            "time_open": open_time_str,
            "risk": round(risk_val, 2),
            "reward": round(reward_val, 2),
            "be_active": "ACTIVE" if be_active else "INACTIVE",
            "trailing_active": "ACTIVE" if trailing_active else "INACTIVE",
            "ai_explanation": ai_explanation
        })
        
    conn.close()
    return enriched

@app.get("/api/orders")
def get_pending_orders():
    """Delegates query of pending orders to mt5."""
    if not state.mt5_connected:
        return []
    raw_orders = mt5.orders_get()
    if not raw_orders:
        return []
    orders = []
    for o in raw_orders:
        orders.append({
            "ticket": o.ticket,
            "symbol": o.symbol,
            "volume": o.volume,
            "type": "BUY_LIMIT" if o.type == mt5.ORDER_TYPE_BUY_LIMIT else "SELL_LIMIT" if o.type == mt5.ORDER_TYPE_SELL_LIMIT else "STOP",
            "price_open": o.price_open,
            "sl": o.sl,
            "tp": o.tp,
            "comment": o.comment,
            "time_setup": getattr(o, "time_setup", o.time_done)
        })
    return orders

@app.get("/api/settings")
def get_dashboard_settings():
    return get_settings()

@app.post("/api/settings")
def save_dashboard_settings(payload: SettingsModel):
    res = save_settings(payload.dict())
    if res:
        return {"status": "SUCCESS", "message": "Settings configuration updated."}
    raise HTTPException(status_code=500, detail="Failed to write configuration variables.")

@app.get("/api/decision")
def get_latest_decision():
    """Queries state cache directly for the latest compiled decision card, fallback to db."""
    state.lock.acquire()
    decision = dict(state.latest_decision)
    exec_candles = list(state.candles)
    state.lock.release()
    
    # Calculate cycle clock
    settings = get_settings()
    exec_tf_str = settings.get("timeframes", {}).get("execution", "M1")
    now_ts = int(time.time())
    cycle_minutes = 1 if exec_tf_str == "M1" else 3 if exec_tf_str == "M3" else 5
    sec_left = (cycle_minutes * 60) - (now_ts % (cycle_minutes * 60))
    time_until_next = f"{sec_left // 60}m {sec_left % 60}s"
    
    decision["time_until_next"] = time_until_next
    return decision

@app.get("/api/history")
def get_trade_history():
    """Queries database for closed trade outcomes log sorted newest first."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT ticket, symbol, direction, volume, entry_price, sl, tp, open_time, status, 
               close_price, close_time, pnl, exit_reason, gross_pnl, net_pnl, duration, 
               strategy_name, confidence_at_entry 
        FROM trades 
        WHERE status='CLOSED' 
        ORDER BY ticket DESC 
        LIMIT 50
        """
    )
    rows = cursor.fetchall()
    conn.close()
    
    history = []
    for r in rows:
        history.append({
            "ticket": r["ticket"],
            "symbol": r["symbol"],
            "direction": r["direction"],
            "volume": r["volume"],
            "entry_price": r["entry_price"],
            "sl": r["sl"] or 0.0,
            "tp": r["tp"] or 0.0,
            "open_time": r["open_time"],
            "status": r["status"],
            "close_price": r["close_price"] or 0.0,
            "close_time": r["close_time"],
            "pnl": r["pnl"],
            "exit_reason": r["exit_reason"],
            "gross_pnl": r["gross_pnl"] or r["pnl"],
            "net_pnl": r["net_pnl"] or r["pnl"],
            "duration": r["duration"] or 0,
            "strategy_name": r["strategy_name"] or "Titan",
            "confidence_at_entry": r["confidence_at_entry"] or 0.70
        })
    return history

@app.get("/api/stats")
def get_performance_stats():
    """Queries recommendation advisors engine offline analytics."""
    return daemon_instance.learning.get_recommendations()

@app.get("/api/learning_diagnostics")
def get_learning_diagnostics():
    """Queries SQLite for the latest closed trade learning metrics and root cause/win analyses."""
    import json
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Get latest closed learning outcome (Rule 10)
        cursor.execute(
            """
            SELECT l.*, t.direction, t.entry_price, t.close_price, t.open_time, t.close_time
            FROM learning_outcomes l
            JOIN trades t ON l.ticket = t.ticket
            ORDER BY l.ticket DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        
        last_outcome = None
        if row:
            last_outcome = {
                "ticket": row["ticket"],
                "symbol": row["symbol"],
                "regime": row["regime"],
                "session": row["session"],
                "pnl": row["pnl"],
                "mfe": row["mfe"],
                "mae": row["mae"],
                "duration_seconds": row["duration_seconds"],
                "timeframe": row["timeframe"],
                "setup_name": row["setup_name"],
                "entry_score": row["entry_score"],
                "sl_hit": row["sl_hit"],
                "tp_hit": row["tp_hit"],
                "manual_exit": row["manual_exit"],
                "smart_exit": row["smart_exit"],
                "screenshot_entry": row["screenshot_entry"],
                "screenshot_exit": row["screenshot_exit"],
                "tick_sequence": json.loads(row["tick_sequence_json"]) if row["tick_sequence_json"] else [],
                "root_cause": json.loads(row["root_cause_json"]) if row["root_cause_json"] else {},
                "win_analysis": json.loads(row["win_analysis_json"]) if row["win_analysis_json"] else {},
                "direction": row["direction"],
                "entry_price": row["entry_price"],
                "close_price": row["close_price"],
                "open_time": row["open_time"],
                "close_time": row["close_time"]
            }
            
        recs = daemon_instance.learning.get_recommendations()
    except Exception as e:
        logger.error(f"Error compiling diagnostics endpoint: {e}")
        last_outcome = None
        recs = {}
    finally:
        conn.close()
        
    return {
        "last_outcome": last_outcome,
        "recommendations": recs
    }

@app.post("/api/halt")
def engage_halt():
    daemon_instance.halt()
    return {"status": "HALTED", "message": "Emergency Stop engaged. Active trades liquidated."}

@app.post("/api/resume")
def engage_resume():
    daemon_instance.resume()
    return {"status": "OPERATIONAL", "message": "Trading resumed. Automatic engine active."}

@app.post("/api/manual_trade")
def manual_trade(payload: ManualTradeRequest):
    """Submits manual order routing execution via background Execution Engine."""
    if not state.mt5_connected:
        raise HTTPException(status_code=503, detail="MT5 connection offline.")
        
    state.lock.acquire()
    bid = state.bid
    ask = state.ask
    state.lock.release()
    
    is_buy = payload.action.upper() == "BUY"
    price = ask if is_buy else bid
    
    # Calculate SL and TP using standard ATR rules
    settings = get_settings()
    atr_multiplier = settings.get("atr_multiplier", 1.5)
    tp_multiplier = settings.get("tp_multiplier", 1.5)
    
    sym_info = mt5.symbol_info(PRIMARY_SYMBOL)
    point = sym_info.point if sym_info else 0.01
    
    # Copy ATR from recent state candles
    state.lock.acquire()
    candles = list(state.candles)
    state.lock.release()
    
    from Titan.market.intelligence.utils import calculate_atr
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    atr_val = calculate_atr(highs, lows, closes, 14) if len(closes) >= 15 else 1.0
    
    atr_points = int(atr_val / point) if point > 0 else 150
    sl_points = max(150, int(atr_points * atr_multiplier))
    tp_points = int(sl_points * tp_multiplier)
    
    if is_buy:
        sl = price - (sl_points * point)
        tp = price + (tp_points * point)
    else:
        sl = price + (sl_points * point)
        tp = price - (tp_points * point)

    # Double order prevention: check if position already exists for safety
    state.lock.acquire()
    current_positions = list(state.open_positions)
    state.lock.release()
    for pos in current_positions:
        if pos["symbol"] == PRIMARY_SYMBOL and pos["type"] == payload.action.upper():
            return {"success": False, "error": "Duplicate order blocked."}
            
    result = daemon_instance.execution.execute_order(
        action=payload.action,
        volume=payload.volume,
        sl=sl,
        tp=tp,
        comment="Manual Web Entry"
    )
    
    if not result.get("success", False):
        return {"success": False, "error": result.get("error", "Execution failed")}
        
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trades (ticket, symbol, direction, volume, entry_price, sl, tp, open_time, status, strategy_name, confidence_at_entry)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'EXECUTED', 'Manual Execution', 1.0)
            """,
            (result["ticket"], PRIMARY_SYMBOL, payload.action.upper(), payload.volume, result["price"], sl, tp)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to record manual trade in SQLite: {e}")
    finally:
        conn.close()
        
    return result

@app.post("/api/close_position")
def close_position(ticket: int):
    """Closes single ticket targeting Execution Engine."""
    success = daemon_instance.execution.close_position_ticket(ticket, comment="Web Panel Request")
    if success:
        conn = get_db_connection()
        try:
            # Sync position exit time in DB
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE trades SET status='CLOSED', close_price=entry_price, close_time=CURRENT_TIMESTAMP, exit_reason='MANUAL_CLOSE' WHERE ticket=?",
                (ticket,)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed database exit log edit: {e}")
        finally:
            conn.close()
        return {"status": "SUCCESS", "message": f"Closed position #{ticket} successfully."}
        
    raise HTTPException(status_code=500, detail="Failed to close position.")

@app.post("/api/close_all")
def close_all_positions():
    """Triggers total liquidation through execution engine."""
    success = daemon_instance.execution.close_all_positions()
    if success:
        return {"status": "SUCCESS", "message": "All open positions liquidated."}
    raise HTTPException(status_code=500, detail="Partial liquidation failed.")

@app.post("/api/backtest")
def run_backtest(req: BacktestRequest):
    results = Backtester.run_historical_backtest(req.symbol, req.preset_range)
    if "error" in results:
        raise HTTPException(status_code=500, detail=results["error"])
    return results

@app.get("/api/candles")
def get_chart_candles(symbol: str = PRIMARY_SYMBOL, count: int = 150):
    """Fetches hist candles from in-memory cache directly instead of querying broker on threads."""
    state.lock.acquire()
    candles = list(state.candles)
    state.lock.release()
    
    if len(candles) >= count:
        return candles[-count:]
    return candles

@app.get("/api/diagnosis")
def get_diagnosis():
    """Compiles hourly self-diagnosis report of recent losses and rejected setups."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Look at recent losing trades
    cursor.execute(
        """
        SELECT ticket, symbol, direction, entry_price, sl, tp, pnl, exit_reason, duration, open_time 
        FROM trades 
        WHERE status='CLOSED' AND pnl < 0 
        ORDER BY ticket DESC 
        LIMIT 5
        """
    )
    rows = cursor.fetchall()
    
    # 2. Look at recent decided WAITs (from choices logged in decisions table)
    cursor.execute(
        """
        SELECT id, timestamp, score, reason, confidence 
        FROM decisions 
        WHERE decision='WAIT' 
        ORDER BY id DESC 
        LIMIT 5
        """
    )
    dec_rows = cursor.fetchall()
    conn.close()
    
    diagnoses = []
    for r in rows:
        ticket = r["ticket"]
        pnl = r["pnl"]
        exit_r = r["exit_reason"] or "Time Out"
        
        # Formulate intelligent text
        reason = "Trend shifted against setup M1 structure. Entered near high/low boundary."
        if "volatility" in exit_r.lower() or "atr" in exit_r.lower():
            reason = "Liquidated due to abnormal volatility spike. ATR threshold breached safety limits."
        elif "trailing" in exit_r.lower():
            reason = "Closed in minor loss via dynamic trailing stop adjustments as momentum faded."
        
        diagnoses.append({
            "type": "TRADE_LOSS",
            "time": r["open_time"],
            "ticket": ticket,
            "pnl": pnl,
            "conclusion": f"Ticket #{ticket} lost {abs(pnl)} USD.",
            "diagnostics": reason,
            "guideline": "Adjust risk scaling down or restrict trading during news overlap sessions."
        })
        
    rejections = []
    for dr in dec_rows:
        reason_txt = dr["reason"] or ""
        desc = "Setup rejected."
        if "Spread" in reason_txt:
            desc = "Trade blocked because broker spread exceeded max allowed points limit."
        elif "NEWS" in reason_txt:
            desc = "Trade blocked because high-impact economic news calendar event was active."
        elif "consensus" in reason_txt.lower() or "marketplace" in reason_txt.lower() or "votes" in reason_txt.lower():
            desc = "Trade blocked due to lack of consensus inside the Strategy Marketplace."
            
        rejections.append({
            "id": dr["id"],
            "time": dr["timestamp"],
            "score": dr["score"],
            "reason": reason_txt,
            "conclusion": desc,
            "recommended_action": "Wait for spreads to narrow or for the strategy voting consensus to align."
        })
        
    # Standard diagnosis hourly conclusion
    conclusion_text = "Operational efficiency is high. Dynamic risk and magic filters are successfully protecting capital."
    if len(diagnoses) >= 3:
        conclusion_text = "Warning: Moderate loss streak detected. Adaptive risk scaling is currently reducing position volume sizes by 50% to shield equity."
        
    return {
        "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        "diagnoses": diagnoses,
        "rejections": rejections,
        "conclusion": conclusion_text
    }

# Initialize statics directory path
static_path = os.path.join(BASE_DIR, "dashboard", "static")
if not os.path.exists(static_path):
    os.makedirs(static_path)

# Serve web content
app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
