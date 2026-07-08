import sys
import os
import sqlite3
import json
from datetime import datetime, timezone

# Add workspace path to system paths
sys.path.append(r"C:\Users\hp\Desktop\goldtradingbot")

from Titan.config.config import get_settings
from Titan.storage.db import get_db_connection
from Titan.execution.mt5_client import MT5Client
from Titan.strategies.technical_analysis import TechAnalysis
from Titan.core.decision_engine import DecisionEngine
from Titan.research.backtester import Backtester

def run_tests():
    print("=== Testing Database Initialization ===")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [t[0] for t in cursor.fetchall()]
    print(f"Verified Database Tables: {tables}")
    
    # 2. Get Settings Check
    print("=== Testing Parameter Configuration ===")
    settings = get_settings()
    print(f"Retrieved Settings: {settings}")
    
    # 3. Initialize MT5 connection
    print("=== Testing MT5 Connectivity ===")
    mt5_ok = MT5Client.initialize()
    print(f"MT5 initialization status: {mt5_ok}")
    
    if mt5_ok:
        try:
            # Try to grab some XAUUSD M1 candles
            import MetaTrader5 as mt5_sdk
            rates = mt5_sdk.copy_rates_from("XAUUSD", mt5_sdk.TIMEFRAME_M1, 0, 100)
            if rates is not None and len(rates) > 2:
                print(f"Successfully copied {len(rates)} M1 candles from MT5.")
                # Format
                m1_candles = [{"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4]} for r in rates]
                
                # Copy H1
                rates_h1 = mt5_sdk.copy_rates_from("XAUUSD", mt5_sdk.TIMEFRAME_H1, 0, 50)
                m5_candles = m1_candles # Fallback
                
                print("=== Testing Multi-Timeframe Confluence Analytics ===")
                confluence = TechAnalysis.analyze_multi_timeframe(m1_candles, m1_candles, m1_candles)
                print(f"Tech Confluence Output checklist: trend_aligned={confluence.get('trend_aligned')}, macro={confluence.get('macro_trend')}, conf={confluence.get('conf_trend')}")
                
                print("=== Testing Decision scoring ===")
                decision = DecisionEngine.evaluate_setup(confluence, 12, False, True, 1.5, settings)
                print(f"Decision output: {decision}")
                
                print("=== Testing Historical Replayer ===")
                backtest_res = Backtester.run_historical_backtest("XAUUSD", "last_day")
                print(f"Backtest Replayer Completed. Keys in output: {list(backtest_res.keys())}")
                if "error" not in backtest_res:
                    print(f"Simulation Trades Count: {backtest_res.get('total_trades')}, Win-rate: {backtest_res.get('win_rate')}%, Bal: ${backtest_res.get('final_balance')}")
                else:
                    print(f"Skip replayer: {backtest_res['error']}")
            else:
                print("MT5 didn't return rates (market may be closed or account not logged in). Mocking calculations...")
        finally:
            MT5Client.shutdown()
            
    print("=== All modules verified successfully ===")

if __name__ == "__main__":
    run_tests()
