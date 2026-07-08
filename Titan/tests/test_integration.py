import sys
import os
import json
import logging

# Ensure the root folder of the project is added to python path
sys.path.insert(0, r"C:\Users\hp\Desktop\goldtradingbot")

from Titan.execution.mt5_client import MT5Client
from Titan.storage.db import get_db_connection
from Titan.core.orchestrator import AutonomousDaemon

# Set logging level
logging.basicConfig(level=logging.INFO)

def main():
    print("========================================")
    print("   PROJECT TITAN V2 INTEGRATION TEST   ")
    print("========================================")
    
    # 1. Test Database
    print("\n[DB] Connecting to SQLite Database storage...")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall()]
        print(f"[DB] Connected. Tables in database: {tables}")
        
        # Count transactions and decisions
        cursor.execute("SELECT COUNT(*) FROM decisions")
        dec_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM trades")
        tr_count = cursor.fetchone()[0]
        print(f"[DB] Current store statistics: decisions logged = {dec_count}, trades logged = {tr_count}")
        conn.close()
    except Exception as e:
        print(f"[DB] ERROR: Connection failed: {e}")
        return

    # 2. Test MT5
    print("\n[MT5] Initializing connection to terminal...")
    success = MT5Client.initialize()
    if not success:
        print("[MT5] ERROR: Failed to interface with MetaTrader 5 terminal.")
        return
        
    try:
        acct = MT5Client.get_account_info()
        if acct:
            print(f"[MT5] Connection established successfully.")
            print(f"[MT5] Account Login: {acct['login']}")
            print(f"[MT5] Account Balance: {acct['currency']} {acct['balance']:.2f}")
            print(f"[MT5] Account Equity: {acct['currency']} {acct['equity']:.2f}")
            print(f"[MT5] Free Margin: {acct['currency']} {acct['margin_free']:.2f}")
        else:
            print("[MT5] ERROR: Connection verified but account info query returned empty.")
            
        tick = MT5Client.get_live_tick("XAUUSD")
        if tick:
            print(f"[MT5] Live price Gold (XAUUSD) quote fetched:")
            print(f"      Bid: {tick['bid']:.3f} | Ask: {tick['ask']:.3f} | Spread: {tick['spread']:.5f}")
        else:
            print("[MT5] WARNING: Bid/Ask request for XAUUSD failed or symbol unavailable.")
            
    finally:
        MT5Client.shutdown()
        print("\n[MT5] MetaTrader 5 client link closed.")

    print("\n========================================")
    print("  INTEGRATION VERIFICATION SUCCESSFUL   ")
    print("========================================")

if __name__ == "__main__":
    main()
