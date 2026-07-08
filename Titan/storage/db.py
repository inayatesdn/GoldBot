import os
import sqlite3
import json
from datetime import datetime
from Titan.config.config import DB_PATH

def get_db_connection():
    db_dir = os.path.dirname(DB_PATH)
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def initialize_database():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Decisions Log
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        symbol TEXT NOT NULL,
        decision TEXT NOT NULL,       -- BUY, SELL, WAIT
        score INTEGER NOT NULL,       -- Total score out of 100
        confidence REAL NOT NULL,      -- Confidence scale 0.0 - 1.0
        reason TEXT,                  -- Long description of logic
        evidence_json TEXT,           -- Detailed indicator/regime payload
        timeframe TEXT NOT NULL
    )
    """)
    
    # Trades Log (active and completed)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        ticket INTEGER PRIMARY KEY,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,      -- BUY, SELL
        volume REAL NOT NULL,
        entry_price REAL NOT NULL,
        sl REAL,
        tp REAL,
        open_time TEXT NOT NULL,      -- SQLite format YYYY-MM-DD HH:MM:SS
        status TEXT NOT NULL,         -- SUBMITTED, EXECUTED, CLOSED, FAILED
        close_price REAL,
        close_time TEXT,
        pnl REAL DEFAULT 0.0,
        regime_at_opening TEXT,
        exit_reason TEXT,
        news_lock_active INTEGER DEFAULT 0,
        gross_pnl REAL DEFAULT 0.0,
        net_pnl REAL DEFAULT 0.0,
        duration INTEGER DEFAULT 0,
        strategy_name TEXT DEFAULT 'Titan Scalper',
        confidence_at_entry REAL DEFAULT 0.70
    )
    """)
    
    # Learning Outcomes (MFE/MAE analytics)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS learning_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket INTEGER UNIQUE,
        symbol TEXT NOT NULL,
        regime TEXT NOT NULL,
        session TEXT NOT NULL,
        indicators_json TEXT,
        pnl REAL NOT NULL,
        mfe REAL NOT NULL,            -- Maximum Favorable Excursion in points
        mae REAL NOT NULL,            -- Maximum Adverse Excursion in points
        duration_seconds INTEGER,
        timeframe TEXT DEFAULT 'M1',
        setup_name TEXT DEFAULT 'Confluence Breakout',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Table Schema Migrations (checks if columns exist and adds them if missing)
    # Trades migrations
    cursor.execute("PRAGMA table_info(trades)")
    trades_cols = [c[1] for c in cursor.fetchall()]
    migration_cols = [
        ("gross_pnl", "REAL DEFAULT 0.0"),
        ("net_pnl", "REAL DEFAULT 0.0"),
        ("duration", "INTEGER DEFAULT 0"),
        ("strategy_name", "TEXT DEFAULT 'Titan Scalper'"),
        ("confidence_at_entry", "REAL DEFAULT 0.70")
    ]
    for col, c_type in migration_cols:
        if col not in trades_cols:
            try:
                cursor.execute(f"ALTER TABLE trades ADD COLUMN {col} {c_type}")
                print(f"[Migration] Added column '{col}' to table 'trades'.")
            except Exception as e:
                print(f"[Migration Warning] Failed to alter column '{col}': {e}")
                
    # Outcomes migrations
    cursor.execute("PRAGMA table_info(learning_outcomes)")
    outcomes_cols = [c[1] for c in cursor.fetchall()]
    outcome_migration_cols = [
        ("timeframe", "TEXT DEFAULT 'M1'"),
        ("setup_name", "TEXT DEFAULT 'Confluence Breakout'"),
        ("entry_score", "INTEGER DEFAULT 0"),
        ("sl_hit", "INTEGER DEFAULT 0"),
        ("tp_hit", "INTEGER DEFAULT 0"),
        ("manual_exit", "INTEGER DEFAULT 0"),
        ("smart_exit", "INTEGER DEFAULT 0"),
        ("screenshot_entry", "TEXT"),
        ("screenshot_exit", "TEXT"),
        ("tick_sequence_json", "TEXT"),
        ("root_cause_json", "TEXT"),
        ("win_analysis_json", "TEXT")
    ]
    for col, c_type in outcome_migration_cols:
        if col not in outcomes_cols:
            try:
                cursor.execute(f"ALTER TABLE learning_outcomes ADD COLUMN {col} {c_type}")
                print(f"[Migration] Added column '{col}' to table 'learning_outcomes'.")
            except Exception as e:
                print(f"[Migration Warning] Failed to alter column '{col}': {e}")
                
    # Settings/System Parameters (For learning feedback updates)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_parameters (
        name TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Scanner History Log
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scanner_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        symbol TEXT NOT NULL,
        timeframe TEXT NOT NULL,
        ohlc_data TEXT,
        indicators_json TEXT,
        structure_json TEXT,
        trend TEXT,
        volatility TEXT,
        session TEXT
    )
    """)
    
    conn.commit()
    conn.close()
    print("SQLite Database initialized successfully at", DB_PATH)

# Initialize on import
initialize_database()
