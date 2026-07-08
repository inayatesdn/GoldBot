import os
import sys
import time
import webbrowser
import logging

# Ensure root dir is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Configure console logger
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

def print_banner():
    banner = """
============================================================
             PROJECT TITAN V2 - AUTONOMOUS TRADING          
============================================================
 [Lead quant architecture, decision confluences, MT5 active]
 
 * Python-first trading engine
 * MetaTrader 5 live execution adapter
 * Local SQLite storage DB
 * Adaptive learning feedback loop (MFE/MAE excursions)
 * Advanced confluences scoring (Trend, Structure, news checks)
 * Modern Champagne Gold & Midnight Obsidian Dashboard
============================================================
"""
    print(banner)

def start_server():
    try:
        import uvicorn
    except ImportError:
        print("[System] Error: 'uvicorn' library not installed. Installing uvicorn...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "uvicorn"])
        import uvicorn
        
    print("[System] Initializing directories and DB schemas...")
    from Titan.storage.db import initialize_database
    initialize_database()
    
    # Auto-open dashboard in browser after a short delay
    def open_browser():
        time.sleep(2.0)
        url = "http://127.0.0.1:8555"
        print(f"[System] Opening Web Dashboard Command Center at: {url}")
        webbrowser.open(url)
        
    import threading
    threading.Thread(target=open_browser, daemon=True).start()
    
    print("[System] Launching FastAPI Web Application and active orchestrator thread...")
    uvicorn.run("Titan.dashboard.app:app", host="0.0.0.0", port=8555, reload=False)

if __name__ == "__main__":
    print_banner()
    start_server()
