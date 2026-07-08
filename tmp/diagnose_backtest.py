import sys
sys.path.insert(0, r"C:\Users\hp\Desktop\goldtradingbot")

import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
from Titan.config.config import get_settings
from Titan.strategies.technical_analysis import TechAnalysis
from Titan.core.decision_engine import DecisionEngine
from Titan.market.sessions import SessionManager
from Titan.market.economic_calendar import EconomicCalendar

def diagnose():
    if not mt5.initialize():
        print("Failed to initialize MT5")
        return
        
    symbol = "XAUUSD"
    mt5.symbol_select(symbol, True)
    
    settings = get_settings()
    print("Settings values:")
    for k, v in settings.items():
        print(f"  {k}: {v}")
        
    utc_now = datetime.now(timezone.utc)
    start_dt = utc_now - timedelta(days=7)
    
    rates_m1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, start_dt, utc_now)
    rates_m3 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M3, start_dt - timedelta(hours=10), utc_now)
    rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_dt - timedelta(hours=20), utc_now)
    
    m1_candles = [{
        "time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]),
        "tick_volume": int(r[5]), "spread": int(r[6])
    } for r in rates_m1]
    
    m3_pool = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rates_m3]
    m5_pool = [{"time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rates_m5]
    
    print(f"\nProcessing {len(m1_candles)} candles...")
    
    rejections = {}
    total_evals = 0
    scores = []
    
    for idx in range(100, len(m1_candles)):
        m1_candle = m1_candles[idx]
        current_time = m1_candle["time"]
        current_price = m1_candle["open"]
        spread_val = m1_candle["spread"]
        
        m1_sub = m1_candles[idx-100:idx]
        m3_sub = [c for c in m3_pool if c["time"] < current_time][-100:]
        m5_sub = [c for c in m5_pool if c["time"] < current_time][-100:]
        
        if len(m3_sub) < 30 or len(m5_sub) < 30:
            continue
            
        total_evals += 1
        confluences = TechAnalysis.analyze_multi_timeframe(m1_sub, m3_sub, m5_sub)
        
        dt_utc = datetime.fromtimestamp(current_time, timezone.utc)
        session_info = SessionManager.get_current_sessions(dt_utc)
        session_desc = session_info["session_desc"]
        session_valid = (settings.get("trading_session") == "All") or (settings.get("trading_session") in session_desc)
        
        news_locked, _, _ = EconomicCalendar.check_news_lock(dt_utc)
        
        dec = DecisionEngine.evaluate_setup(
            confluences, spread_val, news_locked, session_valid, 1.5, settings
        )
        
        scores.append(dec["score"])
        
        if dec["decision"] in ["BUY", "SELL"]:
            if news_locked:
                rejections["news_lock"] = rejections.get("news_lock", 0) + 1
            elif not session_valid:
                rejections["session_invalid"] = rejections.get("session_invalid", 0) + 1
            else:
                print(f"Qualified SIGNAL at Index {idx} / Time {dt_utc}: Shape={dec['decision']}, Score={dec['score']}, Confidence={dec['confidence']}")
        else:
            reasons = dec["reason"].split("; ")
            for r in reasons:
                rejections[r] = rejections.get(r, 0) + 1
                
    print("\nSummary Evaluated Checks:", total_evals)
    print("Max Evaluated Score reached:", max(scores) if scores else 0)
    print("Rejections Frequency:")
    for k, v in sorted(rejections.items(), key=lambda item: item[1], reverse=True):
        print(f"  {k}: {v}")
        
    mt5.shutdown()

if __name__ == "__main__":
    diagnose()
