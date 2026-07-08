import threading
from typing import Dict, Any, List

class TradingState:
    def __init__(self):
        self.lock = threading.Lock() if not hasattr(threading, 'RLock') else threading.RLock()
        
        # Connection status
        self.mt5_connected = False
        self.broker_connection = "DISCONNECTED"
        self.latency_ms = 0
        
        # Live Tick details
        self.bid = 0.0
        self.ask = 0.0
        self.spread = 0.0
        self.server_time = "00:00:00"
        
        # Candles (M1 / current timeline candles)
        self.candles: List[Dict[str, Any]] = []
        
        # Account Details
        self.account = "N/A"
        self.name = "N/A"
        self.server = "N/A"
        self.balance = 0.0
        self.equity = 0.0
        self.margin = 0.0
        self.margin_free = 0.0
        self.margin_level = 0.0
        self.profit = 0.0
        self.currency = "USD"
        
        # Daily Stats
        self.today_closed_pnl = 0.0
        self.today_open_pnl = 0.0
        self.win_rate = 0.0
        self.open_trades_count = 0
        self.current_drawdown_pct = 0.0
        self.daily_risk_used = 0.0
        
        # Current active trades/positions (enriched telemetry format)
        self.open_positions: List[Dict[str, Any]] = []
        
        # Tick-Level Analysis Metrics (Rule 3)
        self.tick_speed = 0.0
        self.tick_velocity = 0.0
        self.tick_acceleration = 0.0
        self.tick_persistence = 0
        self.tick_imbalance = 0.0
        self.tick_vol = 1.0
        self.tick_spread_change = 0.0
        
        # Decision Card snapshot
        self.latest_decision: Dict[str, Any] = {
            "timestamp": "N/A",
            "symbol": "XAUUSD",
            "decision": "WAIT",
            "score": 0,
            "confidence": 0.0,
            "reason": "Initializing quantitative desk...",
            "regime": "N/A",
            "trend": "N/A",
            "momentum": "N/A",
            "volatility": "N/A",
            "structure": "N/A",
            "liquidity": "N/A",
            "entry": 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "expected_rr": "N/A",
            "expected_hold": "N/A",
            "next_setup": "System warm-up in progress...",
            "time_until_next": "0s"
        }
        
        # Interactive controls
        self.auto_trade = False
        self.emergency_halt = False
        self.strategy_status = "PAUSED (OFF)"
        
        # V8 screenshots and tick sequence history trackers (Rule 1)
        self.screenshot_entry_map: Dict[int, str] = {}
        self.screenshot_exit_map: Dict[int, str] = {}
        self.tick_sequence_map: Dict[int, List[float]] = {}

    def to_telemetry_dict(self) -> Dict[str, Any]:
        """Gathers basic account state metrics for API compat."""
        with self.lock:
            return {
                "status": "CONNECTED" if self.mt5_connected else "DISCONNECTED",
                "account": self.account,
                "name": self.name,
                "server": self.server,
                "balance": self.balance,
                "equity": self.equity,
                "margin": self.margin,
                "margin_free": self.margin_free,
                "margin_level": self.margin_level,
                "currency": self.currency,
                "profit": self.profit,
                "today_closed_pnl": self.today_closed_pnl,
                "today_open_pnl": self.today_open_pnl,
                "win_rate": self.win_rate,
                "open_trades_count": self.open_trades_count,
                "current_drawdown_pct": self.current_drawdown_pct,
                "daily_risk_used": self.daily_risk_used,
                "current_spread": self.spread,
                "current_atr": self.latest_decision.get("volatility", 0.0),
                "bid": self.bid,
                "ask": self.ask,
                "server_time": self.server_time,
                "mt5_latency_ms": self.latency_ms,
                "broker_connection": self.broker_connection,
                "strategy_status": self.strategy_status,
                "emergency_halt": self.emergency_halt,
                "auto_trade": self.auto_trade,
                "tick_speed": self.tick_speed,
                "tick_velocity": self.tick_velocity,
                "tick_acceleration": self.tick_acceleration,
                "tick_persistence": self.tick_persistence,
                "tick_imbalance": self.tick_imbalance,
                "tick_vol": self.tick_vol,
                "tick_spread_change": self.tick_spread_change,
                "latest_decision": self.latest_decision,
                "system_status": {
                    "market_feed": "Connected" if self.mt5_connected else "Disconnected",
                    "broker": "Connected" if self.mt5_connected else "Disconnected",
                    "execution_engine": "Running" if (self.auto_trade and not self.emergency_halt) else "Paused",
                    "risk_engine": "Running" if self.mt5_connected else "Offline",
                    "learning_engine": "Running",
                    "position_manager": "Running" if self.mt5_connected else "Offline",
                    "journal": "Running",
                    "latency_ms": self.latency_ms,
                    "eval_cycle": f"Symbol: XAUUSD | TF: M1"
                }
            }

# Global singleton thread-safe state
state = TradingState()
