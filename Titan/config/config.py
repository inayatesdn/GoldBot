import os
import sqlite3
import json
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

# Base Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "storage", "titan.db")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

# MT5 Terminal Settings
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"
MT5_LOGIN = os.getenv("MT5_LOGIN", "")
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "MetaQuotes-Demo")

# Target Market Symbols
PRIMARY_SYMBOL = "XAUUSD"
MONITORED_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD"]
CROSS_MARKET_SYMBOLS = {
    "DXY": "DXYZ",      # DXY index symbol
    "US10Y": "US10Y",   # US 10 year Treasury yield symbol
    "SP500": "US500",   # S&P 500 index symbol
    "BTC": "BTCUSD"     # Bitcoin symbol
}

# Static Default Settings (used if DB does not have override settings)
DEFAULT_SETTINGS = {
    "risk_pct": 1.0,
    "max_daily_loss": 3.0,
    "max_concurrent_positions": 2,
    "trading_session": "London-New York Overlap", # Options: Sydney, Tokyo, London, NY, overlaps
    "timeframes": {
        "execution": "M1",
        "confirmation": "M3",
        "trend": "M5"
    },
    "confidence_threshold": 0.70,
    "atr_multiplier": 1.5,
    "tp_multiplier": 1.5,
    "news_lock": True,
    "spread_limit": 300,        # in points
    "slippage_limit": 30,       # in points
    "auto_trade": False         # Auto execution toggle
}

def get_settings():
    """
    Retrieves dynamic settings parameters from the SQLite database.
    If database does not contain overrides, returns DEFAULT_SETTINGS.
    """
    settings = DEFAULT_SETTINGS.copy()
    
    # Read directly from SQL database to prevent circular imports
    if not os.path.exists(DB_PATH):
        return settings
        
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Verify system_parameters table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='system_parameters';")
        if not cursor.fetchone():
            conn.close()
            return settings
            
        cursor.execute("SELECT value FROM system_parameters WHERE name = 'user_settings';")
        row = cursor.fetchone()
        conn.close()
        
        if row:
            db_setting = json.loads(row["value"])
            # Update settings dict
            for k, v in db_setting.items():
                settings[k] = v
    except Exception:
        # Fallback to default configs quietly if DB locked or reading failed
        pass
        
    return settings

def save_settings(new_settings):
    """
    Saves user parameters back to database.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Ensure table exists
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_parameters (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        cursor.execute(
            "INSERT OR REPLACE INTO system_parameters (name, value, updated_at) VALUES ('user_settings', ?, CURRENT_TIMESTAMP)",
            (json.dumps(new_settings),)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Error saving settings parameter: {e}")
        return False

# Retro-compatible parameters mapped to default values
MAX_RISK_PER_TRADE_PCT = DEFAULT_SETTINGS["risk_pct"]
MAX_DAILY_LOSS_PCT = DEFAULT_SETTINGS["max_daily_loss"]
MAX_CONCURRENT_TRADES = DEFAULT_SETTINGS["max_concurrent_positions"]
MIN_RISK_REWARD_RATIO = DEFAULT_SETTINGS["tp_multiplier"]
MAX_SPREAD_POINTS = DEFAULT_SETTINGS["spread_limit"]
BREAK_EVEN_RR_THRESHOLD = 1.0
PARTIAL_CLOSE_RR_THRESHOLD = 1.0
NEWS_LOCK_MINUTES_PRE = 30
NEWS_LOCK_MINUTES_POST = 30
LIVE_EXECUTION = DEFAULT_SETTINGS["auto_trade"]
DEMO_TESTING = True

# Visual Overlay Styles
COLORS = {
    "primary": "#ffb703",       # Champagne gold
    "bg_dark": "#0d0e12",       # Midnight obsidian
    "bg_card": "#181a20",       # Sleek card color
    "accent": "#00f5d4",        # Neon green
    "danger": "#ff007f",        # Hot pink/red
    "neutral": "#e0e1dd",       # Platinum white
    "grid": "#22252a"           # Deep grid lines
}
