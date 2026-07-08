import os
import threading
import logging
import json
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import time
from datetime import datetime, timezone
import MetaTrader5 as mt5

from Titan.config.config import BASE_DIR, PRIMARY_SYMBOL, get_settings, save_settings
from Titan.storage.db import get_db_connection
from Titan.execution.mt5_client import MT5Client
from Titan.core.orchestrator import AutonomousDaemon
from Titan.research.backtester import Backtester
from Titan.learning.learning_engine import LearningEngine

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

# Global Daemon Reference
daemon_instance = AutonomousDaemon()
daemon_thread = None

class BacktestRequest(BaseModel):
    symbol: str = "XAUUSD"
    preset_range: str = "last_week" # last_day, last_week, last_month

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
    global daemon_thread, daemon_instance
    MT5Client.initialize()
    
    daemon_thread = threading.Thread(target=daemon_instance.start_loop, daemon=True)
    daemon_thread.start()
    logger.info("Autonomous orchestrator daemon started successfully alongside FastAPI server.")

@app.on_event("shutdown")
def shutdown_event():
    global daemon_instance
    daemon_instance.is_running = False
    MT5Client.shutdown()
    logger.info("Server shutdown client releases completed.")

@app.get("/api/telemetry")
def get_telemetry():
    """Returns MT5 connection and account health statistics enriched with all required metrics."""
    acct = MT5Client.get_account_info()
    conn_ok = MT5Client.check_connection()
    settings = get_settings()
    
    # Latency calculation
    t0 = time.time()
    if conn_ok:
        mt5.terminal_info()
    latency_ms = int((time.time() - t0) * 1000)
    
    # Current Tick, Bid, Ask, Spread & Server Time details
    live_tick = MT5Client.get_live_tick(PRIMARY_SYMBOL)
    symbol_inf = mt5.symbol_info(PRIMARY_SYMBOL)
    point = symbol_inf.point if symbol_inf else 0.01
    current_spread = round(live_tick["spread"] / point) if live_tick and point > 0 else 0.0
    
    bid_val = live_tick["bid"] if live_tick else 0.0
    ask_val = live_tick["ask"] if live_tick else 0.0
    server_time_sec = live_tick["time"] if live_tick else int(time.time())
    server_time_str = datetime.fromtimestamp(server_time_sec, timezone.utc).strftime('%H:%M:%S')
    
    # Calculate ATR
    rates = mt5.copy_rates_from_pos(PRIMARY_SYMBOL, mt5.TIMEFRAME_M1, 0, 15)
    current_atr = 0.0
    if rates is not None and len(rates) > 2:
        cls_arr = [r[4] for r in rates]
        hi_arr = [r[2] for r in rates]
        lo_arr = [r[3] for r in rates]
        from Titan.market.scanner import MultiTimeframeScanner
        current_atr = round(MultiTimeframeScanner.calculate_atr(hi_arr, lo_arr, cls_arr, 14) / point) if point > 0 else 0.0
        
    # Calculate closed profit and win rate TODAY directly from MT5 deals (no caching)
    today_closed = 0.0
    total_today = 0
    wins_today = 0
    current_utc_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    if conn_ok:
        day_deals = MT5Client.get_closed_trades(days=1)
        for deal in day_deals:
            if deal["time"].startswith(current_utc_date):
                pnl = deal["pnl"]
                today_closed += pnl
                total_today += 1
                if pnl > 0:
                    wins_today += 1
                    
    today_win_rate = (wins_today / total_today * 100.0) if total_today > 0 else 0.0
    
    # Open positions
    open_positions = MT5Client.get_open_positions()
    open_trades_count = len(open_positions)
    floating_profit = sum(p["profit"] for p in open_positions)
    
    daily_risk_used = 0.0
    mult = 100.0 if "XAU" in PRIMARY_SYMBOL or "GOLD" in PRIMARY_SYMBOL else 100000.0
    for p in open_positions:
        if p["sl"] > 0:
            daily_risk_used += abs(p["price_open"] - p["sl"]) * p["volume"] * mult
            
    # Get active timeframe
    tf_config = settings.get("timeframes", {})
    exec_tf_str = tf_config.get("execution", "M1")
    
    if not conn_ok or acct is None:
        return {
            "status": "DISCONNECTED",
            "account": "N/A",
            "balance": 0.0,
            "equity": 0.0,
            "margin": 0.0,
            "margin_free": 0.0,
            "margin_level": 0.0,
            "profit": 0.0,
            "today_closed_pnl": 0.0,
            "today_open_pnl": 0.0,
            "win_rate": 0.0,
            "open_trades_count": 0,
            "current_drawdown_pct": 0.0,
            "daily_risk_used": 0.0,
            "current_spread": 0.0,
            "current_atr": 0.0,
            "bid": 0.0,
            "ask": 0.0,
            "server_time": "00:00:00",
            "mt5_latency_ms": 0,
            "broker_connection": "DISCONNECTED",
            "strategy_status": "PAUSED",
            "emergency_halt": daemon_instance.emergency_halt,
            "auto_trade": settings.get("auto_trade", False),
            "system_status": {
                "market_feed": "Disconnected",
                "broker": "Disconnected",
                "execution_engine": "Paused",
                "risk_engine": "Offline",
                "learning_engine": "Running",
                "position_manager": "Offline",
                "journal": "Running",
                "latency_ms": 0,
                "eval_cycle": f"Symbol: {PRIMARY_SYMBOL} | TF: {exec_tf_str}"
            }
        }
        
    initial_balance = acct["balance"]
    current_equity = acct["equity"]
    drawdown_pct = max(0.0, (initial_balance - current_equity) / initial_balance * 100.0) if initial_balance > 0 else 0.0
    
    return {
        "status": "CONNECTED",
        "account": acct["login"],
        "name": acct["name"],
        "server": acct["server"],
        "balance": acct["balance"],
        "equity": acct["equity"],
        "margin": acct["margin"],
        "margin_free": acct["margin_free"],
        "margin_level": acct["margin_level"],
        "currency": acct["currency"],
        "profit": acct["profit"],
        "today_closed_pnl": round(today_closed, 2),
        "today_open_pnl": round(floating_profit, 2),
        "win_rate": round(today_win_rate, 2),
        "open_trades_count": open_trades_count,
        "current_drawdown_pct": round(drawdown_pct, 2),
        "daily_risk_used": round(daily_risk_used, 2),
        "current_spread": current_spread,
        "current_atr": current_atr,
        "bid": bid_val,
        "ask": ask_val,
        "server_time": server_time_str,
        "mt5_latency_ms": latency_ms,
        "broker_connection": "CONNECTED",
        "strategy_status": "RUNNING (ACTIVE)" if (settings.get("auto_trade", False) and not daemon_instance.emergency_halt) else "PAUSED (OFF)",
        "emergency_halt": daemon_instance.emergency_halt,
        "auto_trade": settings.get("auto_trade", False),
        "system_status": {
            "market_feed": "Connected",
            "broker": "Connected",
            "execution_engine": "Running" if (settings.get("auto_trade", False) and not daemon_instance.emergency_halt) else "Paused",
            "risk_engine": "Running",
            "learning_engine": "Running",
            "position_manager": "Running",
            "journal": "Running",
            "latency_ms": latency_ms,
            "eval_cycle": f"Symbol: {PRIMARY_SYMBOL} | TF: {exec_tf_str}"
        }
    }

@app.get("/api/positions")
def get_positions():
    """Returns list of open positions from MT5 enriched with risk telemetry."""
    positions = MT5Client.get_open_positions()
    
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
            
        open_time_str = datetime.fromtimestamp(pos["time"], timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        # Load trade record from DB if present
        cursor.execute("SELECT strategy_name, confidence_at_entry FROM trades WHERE ticket = ?", (ticket,))
        tr_row = cursor.fetchone()
        
        strategy_name = tr_row["strategy_name"] if tr_row else "Titan Scalper"
        confidence = tr_row["confidence_at_entry"] if tr_row else 0.70
        
        # Cross-reference decisions table to fetch the AI rationale
        cursor.execute("SELECT reason FROM decisions WHERE timestamp <= ? ORDER BY id DESC LIMIT 1", (open_time_str,))
        dec_row = cursor.fetchone()
        ai_explanation = dec_row["reason"] if dec_row else "Managing open low-timeframe confluence scalping position."
        
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
    """Returns list of pending orders."""
    return MT5Client.get_pending_orders()

@app.get("/api/settings")
def get_dashboard_settings():
    """Reads configuration profile profiles."""
    return get_settings()

@app.post("/api/settings")
def save_dashboard_settings(payload: SettingsModel):
    """Saves new client configuration parameters."""
    res = save_settings(payload.dict())
    if res:
        return {"status": "SUCCESS", "message": "Settings updated in database successfully."}
    raise HTTPException(status_code=500, detail="Failed to save settings variables.")

@app.get("/api/decision")
def get_latest_decision():
    """Queries SQLite store for the latest quantitative decision card."""
    settings = get_settings()
    tf_config = settings.get("timeframes", {})
    exec_tf_str = tf_config.get("execution", "M1")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT timestamp, symbol, decision, score, confidence, reason, evidence_json FROM decisions ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    
    # Estimate time until next evaluation cycle
    now_ts = int(time.time())
    cycle_minutes = 1 if exec_tf_str == "M1" else 3 if exec_tf_str == "M3" else 5
    seconds_in_cycle = cycle_minutes * 60
    sec_left = seconds_in_cycle - (now_ts % seconds_in_cycle)
    time_until_next = f"{sec_left // 60}m {sec_left % 60}s"
    
    if row:
        evidence = {}
        try:
            evidence = json.loads(row["evidence_json"])
        except Exception:
            pass
            
        confluences = evidence.get("confluences", {})
        breakdown = evidence.get("breakdown", {})
        
        entry_p = confluences.get("m_metrics", {}).get("close", 2000.0) if isinstance(confluences.get("m_metrics"), dict) else 2000.0
        atr_pts = confluences.get("m_metrics", {}).get("atr_14", 1.5) if isinstance(confluences.get("m_metrics"), dict) else 1.5
        
        sl_points = max(150, int((atr_pts / 0.01) * 1.5))
        tp_points = int(sl_points * 1.5)
        
        if row["decision"] == "BUY":
            sl = entry_p - (sl_points * 0.01)
            tp = entry_p + (tp_points * 0.01)
        elif row["decision"] == "SELL":
            sl = entry_p + (sl_points * 0.01)
            tp = entry_p - (tp_points * 0.01)
        else:
            sl = 0.0
            tp = 0.0
            
        next_setup = "Awaiting swing high/low breakout confirmation..."
        if row["decision"] == "WAIT":
            if "trend" in row["reason"].lower():
                next_setup = "Waiting for Execution, Confirmation, and Macro trends to align."
            elif "spread" in row["reason"].lower():
                next_setup = "Expecting spread contraction below limit."
            elif "volatility" in row["reason"].lower():
                next_setup = "Awaiting volatility expansion (ATR surge)."
            elif "confidence" in row["reason"].lower():
                next_setup = "Awaiting high-probability SMC structure confirmation."
                
        return {
            "timestamp": row["timestamp"],
            "symbol": row["symbol"],
            "decision": row["decision"],
            "score": row["score"],
            "confidence": row["confidence"],
            "reason": row["reason"],
            "regime": evidence.get("regime", "Trending"),
            "trend": f"M5: {confluences.get('macro_trend', 'N/A')} | M3: {confluences.get('conf_trend', 'N/A')} | M1: {confluences.get('exec_trend', 'N/A')}",
            "momentum": f"RSI: {round(confluences.get('m_metrics', {}).get('rsi', 50.0), 1) if isinstance(confluences.get('m_metrics'), dict) else '50.0'}",
            "volatility": f"ATR points: {round(atr_pts / 0.01) if atr_pts else 150}",
            "structure": "BOS/CHoCH Active" if (isinstance(confluences.get("m_metrics"), dict) and (confluences.get("m_metrics", {}).get("bos") or confluences.get("m_metrics", {}).get("choch"))) else "Consolidating",
            "liquidity": "Swept OB/FVG" if (isinstance(confluences.get("m_metrics"), dict) and (confluences.get("m_metrics", {}).get("ob_touched") or confluences.get("m_metrics", {}).get("fvg_touched"))) else "Normal",
            "execution_score": row["score"],
            "risk_score": breakdown.get("spread_quality", 5) + breakdown.get("news_filter", 5),
            "entry": round(entry_p, 3) if entry_p > 0 else 0.0,
            "sl": round(sl, 3) if sl > 0 else 0.0,
            "tp": round(tp, 3) if tp > 0 else 0.0,
            "expected_rr": "1:1.5 (composite)",
            "expected_hold": "15-20 min",
            "next_setup": next_setup,
            "time_until_next": time_until_next
        }
    else:
        return {
            "timestamp": "N/A",
            "symbol": PRIMARY_SYMBOL,
            "decision": "WAIT",
            "score": 0,
            "confidence": 0.0,
            "reason": "Tracking multi-timeframe concordance signals...",
            "regime": "N/A",
            "trend": "N/A",
            "momentum": "N/A",
            "volatility": "N/A",
            "structure": "N/A",
            "liquidity": "N/A",
            "execution_score": 0,
            "risk_score": 0,
            "entry": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "expected_rr": "N/A",
            "expected_hold": "N/A",
            "next_setup": "Awaiting market stream alignment...",
            "time_until_next": time_until_next
        }

@app.get("/api/history")
def get_trade_history():
    """Queries unique closed trades from SQLite database, sorted newest first and limited to 50."""
    conn = get_db_connection()
    try:
        from Titan.learning.learning_engine import LearningEngine
        LearningEngine.process_completed_trades(conn, MT5Client)
    except Exception as e:
        logger.error(f"Error synchronizing live trade history: {e}")
        
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
            "sl": r["sl"] if r["sl"] is not None else 0.0,
            "tp": r["tp"] if r["tp"] is not None else 0.0,
            "open_time": r["open_time"],
            "status": r["status"],
            "close_price": r["close_price"] if r["close_price"] is not None else 0.0,
            "close_time": r["close_time"],
            "pnl": r["pnl"],
            "exit_reason": r["exit_reason"],
            "gross_pnl": r["gross_pnl"] if r["gross_pnl"] is not None else r["pnl"],
            "net_pnl": r["net_pnl"] if r["net_pnl"] is not None else r["pnl"],
            "duration": r["duration"] if r["duration"] is not None else 0,
            "strategy_name": r["strategy_name"] if r["strategy_name"] is not None else "Titan Scalper",
            "confidence_at_entry": r["confidence_at_entry"] if r["confidence_at_entry"] is not None else 0.70
        })
    return history

@app.get("/api/stats")
def get_performance_stats():
    """Aggregates learning diagnostics metrics from database tables."""
    conn = get_db_connection()
    try:
        results = LearningEngine.analyze_performance(conn)
        return results
    except Exception as e:
        logger.error(f"Failed to load performance metrics: {e}")
        return {
            "status": "error",
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
    finally:
        conn.close()

@app.post("/api/halt")
def engage_halt():
    """Liquidates and halts orchestrator."""
    global daemon_instance
    daemon_instance.halt()
    return {"status": "HALTED", "message": "Emergency Stop engaged. Active trades liquidated."}

@app.post("/api/resume")
def engage_resume():
    """Disengages halt."""
    global daemon_instance
    daemon_instance.resume()
    return {"status": "OPERATIONAL", "message": "Trading resumed. Automatic engine active."}

@app.post("/api/manual_trade")
def manual_trade(payload: ManualTradeRequest):
    """Executes a manual market order using the MT5 client."""
    if not MT5Client.check_connection():
        raise HTTPException(status_code=503, detail="MT5 connection offline.")
    
    symbol = PRIMARY_SYMBOL
    symbol_info = MT5Client.get_symbol_info(symbol)
    if not symbol_info:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} info not found.")
        
    live_tick = MT5Client.get_live_tick(symbol)
    if not live_tick:
        raise HTTPException(status_code=500, detail="Failed to fetch live ticks.")
        
    is_buy = payload.action.upper() == "BUY"
    price = live_tick["ask"] if is_buy else live_tick["bid"]
    
    settings = get_settings()
    atr_multiplier = settings.get("atr_multiplier", 1.5)
    tp_multiplier = settings.get("tp_multiplier", 1.5)
    
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, 15)
    point = symbol_info.point
    atr_points = 150
    if rates is not None and len(rates) > 2:
        cls_arr = [r[4] for r in rates]
        hi_arr = [r[2] for r in rates]
        lo_arr = [r[3] for r in rates]
        from Titan.market.scanner import MultiTimeframeScanner
        atr_val = MultiTimeframeScanner.calculate_atr(hi_arr, lo_arr, cls_arr, 14)
        atr_points = int(atr_val / point) if point > 0 else 150
        
    sl_points = max(150, int(atr_points * atr_multiplier))
    tp_points = int(sl_points * tp_multiplier)
    
    if is_buy:
        sl = price - (sl_points * point)
        tp = price + (tp_points * point)
    else:
        sl = price + (sl_points * point)
        tp = price - (tp_points * point)
        
    result = MT5Client.execute_order(
        symbol=symbol,
        action=payload.action,
        volume=payload.volume,
        sl=sl,
        tp=tp,
        comment="Titan Manual Panel Execute"
    )
    
    if not result.get("success", False):
        return {"success": False, "error": result.get("error", "Execution failed")}
        
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trades (ticket, symbol, direction, volume, entry_price, sl, tp, open_time, status, strategy_name, confidence_at_entry)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EXECUTED', 'Manual Execution', 1.0)
            """,
            (result["ticket"], symbol, payload.action.upper(), payload.volume, result["price"], sl, tp,
             datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), 'EXECUTED')
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to record manual trade in DB: {e}")
    finally:
        conn.close()
        
    return result

@app.post("/api/close_position")
def close_position(ticket: int):
    """Liquidates a single open position."""
    if not MT5Client.check_connection():
        raise HTTPException(status_code=503, detail="MT5 connection offline.")
        
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise HTTPException(status_code=404, detail=f"Position {ticket} not found.")
        
    pos = positions[0]
    success = MT5Client.close_position(ticket, comment="Manual Panel Single Close")
    
    if success:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE trades SET status='CLOSED', close_price=?, close_time=?, exit_reason='MANUAL_CLOSE' WHERE ticket=?",
                (pos.price_current, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), ticket)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to update closed trade in DB: {e}")
        finally:
            conn.close()
        return {"status": "SUCCESS", "message": f"Position {ticket} liquidated."}
    
    raise HTTPException(status_code=500, detail=f"Failed to close position {ticket}.")

@app.post("/api/close_all")
def close_all_positions():
    """Liquidates all open positions."""
    if not MT5Client.check_connection():
        raise HTTPException(status_code=503, detail="MT5 connection offline.")
        
    open_positions = MT5Client.get_open_positions()
    closed_tickets = []
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for pos in open_positions:
        ticket = pos["ticket"]
        success = MT5Client.close_position(ticket, comment="Manual Liquidation Request")
        if success:
            closed_tickets.append(ticket)
            cursor.execute(
                "UPDATE trades SET status='CLOSED', close_price=?, close_time=?, exit_reason='MANUAL_CLOSE' WHERE ticket=?",
                (pos["price_current"], datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), ticket)
            )
            
    conn.commit()
    conn.close()
    
    return {"status": "SUCCESS", "message": f"Liquidation complete. Closed {len(closed_tickets)} positions.", "tickets": closed_tickets}

@app.post("/api/backtest")
def run_backtest(req: BacktestRequest):
    """Triggers standard backtest replayer."""
    results = Backtester.run_historical_backtest(
        req.symbol, req.preset_range
    )
    if "error" in results:
        raise HTTPException(status_code=500, detail=results["error"])
    return results

@app.get("/api/candles")
def get_chart_candles(symbol: str = PRIMARY_SYMBOL, count: int = 150):
    """Fetches historic bars to display on UI charts."""
    if not MT5Client.check_connection():
        logger.error("MT5 check connection returned False inside /api/candles")
        return []
    selected = mt5.symbol_select(symbol, True)
    logger.info(f"Symbol select in endpoint: {selected} for {symbol}")
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, count)
    
    if rates is None:
        logger.error(f"copy_rates_from_pos returned None. Error: {mt5.last_error()}")
        return []
        
    candles = []
    for r in rates:
        candles.append({
            "time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4])
        })
    return candles

# Initialize statics directory path
static_path = os.path.join(BASE_DIR, "dashboard", "static")
if not os.path.exists(static_path):
    os.makedirs(static_path)

# Serve web content
app.mount("/", StaticFiles(directory=static_path, html=True), name="static")
