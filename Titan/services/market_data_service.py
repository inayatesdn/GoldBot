import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, MT5_PATH, MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, get_settings
from Titan.core.state import state
from Titan.core.logger import system_logger, trading_logger

class MarketDataService:
    def __init__(self, symbol: str = PRIMARY_SYMBOL):
        self.symbol = symbol
        self.logger = system_logger
        self.last_tick_time = 0
        self.tf_map = {
            "M1": (mt5.TIMEFRAME_M1, 60),
            "M3": (mt5.TIMEFRAME_M3, 180),
            "M5": (mt5.TIMEFRAME_M5, 300),
            "M15": (mt5.TIMEFRAME_M15, 900),
            "M30": (mt5.TIMEFRAME_M30, 1800),
            "H1": (mt5.TIMEFRAME_H1, 3600),
        }
        self.prev_tick_time_msc = 0
        self.prev_price = 0.0
        self.prev_velocity = 0.0
        self.prev_spread = 0.0
        self.tick_persistence = 0
        
    def start(self):
        """Initializes the database candle history state."""
        self.logger.info("Starting Market Data Service...")
        self.reconnect_if_needed()
        self.initialize_candles_from_terminal()

    def reconnect_if_needed(self) -> bool:
        """Dedicated connection manager to dynamically check and restore the MT5 link."""
        # 3. Connection Manager
        try:
            terminal_info = mt5.terminal_info()
            if terminal_info is None or not terminal_info.connected:
                state.lock.acquire()
                state.mt5_connected = False
                state.broker_connection = "DISCONNECTED"
                state.lock.release()
                
                self.logger.warning("MetaTrader 5 connection offline. Re-initializing...")
                
                # Check terminal path and login info
                if MT5_LOGIN and MT5_PASSWORD:
                    success = mt5.initialize(
                        path=MT5_PATH,
                        login=int(MT5_LOGIN),
                        password=MT5_PASSWORD,
                        server=MT5_SERVER
                    )
                else:
                    success = mt5.initialize(path=MT5_PATH)
                    
                if success:
                    t_info = mt5.terminal_info()
                    self.logger.info(f"Successfully reconnected to MT5. Terminal active: {t_info.connected if t_info else False}")
                    state.lock.acquire()
                    state.mt5_connected = True
                    state.broker_connection = "CONNECTED"
                    state.name = getattr(t_info, "name", "N/A")
                    state.server = getattr(t_info, "server", "N/A")
                    state.lock.release()
                    return True
                else:
                    self.logger.error(f"MT5 reconnection failed. Error code: {mt5.last_error()}")
                    return False
            else:
                state.lock.acquire()
                state.mt5_connected = True
                state.broker_connection = "CONNECTED"
                state.lock.release()
                return True
        except Exception as e:
            self.logger.error(f"Exception during MT5 connection check: {e}")
            state.lock.acquire()
            state.mt5_connected = False
            state.broker_connection = "DISCONNECTED"
            state.lock.release()
            return False

    def initialize_candles_from_terminal(self):
        """Loads historical candles on startup or reconnect to initialize our builder."""
        if not state.mt5_connected:
            return
            
        settings = get_settings()
        exec_tf_str = settings.get("timeframes", {}).get("execution", "M1")
        mt5_tf, _ = self.tf_map.get(exec_tf_str, (mt5.TIMEFRAME_M1, 60))
        
        self.logger.info(f"Initializing in-memory candles for symbol {self.symbol} ({exec_tf_str})...")
        rates = mt5.copy_rates_from_pos(self.symbol, mt5_tf, 0, 150)
        
        if rates is not None and len(rates) > 0:
            candles = []
            for r in rates:
                candles.append({
                    "time": int(r[0]),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "tick_volume": int(r[5])
                })
            state.lock.acquire()
            state.candles = candles
            state.lock.release()
            self.logger.info(f"Loaded {len(rates)} candles from MT5 broker.")
        else:
            self.logger.error(f"Failed to copy historical rates: {mt5.last_error()}")

    def update_tick(self) -> Dict[str, Any]:
        """Fetches the latest tick, updates telemetry and feeds the candle builder."""
        if not state.mt5_connected:
            return None
            
        t0 = time.time()
        tick = mt5.symbol_info_tick(self.symbol)
        latency_ms = int((time.time() - t0) * 1000)
        
        if tick is None:
            self.logger.warning(f"Failed to fetch tick for symbol {self.symbol}. Error code: {mt5.last_error()}")
            return None
            
        point = 0.01  # Default point
        sym_info = mt5.symbol_info(self.symbol)
        if sym_info:
            point = sym_info.point
            
        bid = float(tick.bid)
        ask = float(tick.ask)
        spread_pts = round(abs(ask - bid) / point) if point > 0 else 10
        server_sec = tick.time
        server_dt_str = datetime.fromtimestamp(server_sec, timezone.utc).strftime('%H:%M:%S')
        
        # Check if spread is valid or ask/bid are zeroes (Detect login changes / Symbol unavailable)
        if bid <= 0.0 or ask <= 0.0:
            self.logger.error(f"Invalid live prices received: Bid={bid}, Ask={ask}. Skipping tick.")
            return None

        # --- 1. Tick-Level Calculations (Rule 3) ---
        now_msc = getattr(tick, 'time_msc', 0)
        if now_msc == 0:
            now_msc = int(time.time() * 1000)
            
        # Tick Speed (ms duration between tick arrivals)
        time_delta_ms = float(now_msc - self.prev_tick_time_msc) if self.prev_tick_time_msc > 0 else 100.0
        if time_delta_ms <= 0:
            time_delta_ms = 1.0 # Ensure no division by zero
        self.prev_tick_time_msc = now_msc
        
        # Price (mid-point price)
        price = (bid + ask) / 2.0
        price_delta_pts = (price - self.prev_price) / point if self.prev_price > 0.0 else 0.0
        
        # Price Velocity (points / second)
        velocity_pts_sec = float(price_delta_pts / (time_delta_ms / 1000.0))
        
        # Price Acceleration (pts / sec^2)
        acceleration_pts_sec2 = float(velocity_pts_sec - self.prev_velocity) / (time_delta_ms / 1000.0) if self.prev_velocity != 0.0 else 0.0
        self.prev_velocity = velocity_pts_sec
        self.prev_price = price
        
        # Persistence (consecutive ticks in same direction)
        if price_delta_pts > 0:
            if self.tick_persistence >= 0:
                self.tick_persistence += 1
            else:
                self.tick_persistence = 1
        elif price_delta_pts < 0:
            if self.tick_persistence <= 0:
                self.tick_persistence -= 1
            else:
                self.tick_persistence = -1
                
        # Imbalance (bid/ask volume imbalance if present, else directionally weighted)
        imbalance = 0.0
        try:
            bid_vol = float(getattr(tick, 'bid_volume', 0.0))
            ask_vol = float(getattr(tick, 'ask_volume', 0.0))
            if bid_vol > 0 or ask_vol > 0:
                imbalance = (bid_vol - ask_vol) / max(1.0, bid_vol + ask_vol)
            else:
                imbalance = 1.0 if price_delta_pts > 0 else (-1.0 if price_delta_pts < 0 else 0.0)
        except Exception:
            imbalance = 1.0 if price_delta_pts > 0 else (-1.0 if price_delta_pts < 0 else 0.0)
            
        # Spread Change Delta
        spread_delta = float(spread_pts - self.prev_spread) if self.prev_spread > 0.0 else 0.0
        self.prev_spread = float(spread_pts)
        
        # Tick Volume (actual tick volume or 1.0)
        tick_vol = float(getattr(tick, 'volume_real', 1.0))
        if tick_vol <= 0:
            tick_vol = float(getattr(tick, 'volume', 1.0))
            
        state.lock.acquire()
        state.bid = bid
        state.ask = ask
        state.spread = spread_pts
        state.server_time = server_dt_str
        state.latency_ms = latency_ms
        
        # Assign tick metrics directly to global state so WebSocket sends them to dashboard
        state.tick_speed = time_delta_ms
        state.tick_velocity = velocity_pts_sec
        state.tick_acceleration = acceleration_pts_sec2
        state.tick_persistence = self.tick_persistence
        state.tick_imbalance = imbalance
        state.tick_vol = tick_vol
        state.tick_spread_change = spread_delta
        state.lock.release()
        
        # Feed the in-memory candle builder
        self.build_candles_in_memory(tick, bid, ask)
        
        return {
            "bid": bid,
            "ask": ask,
            "spread": spread_pts,
            "time": server_sec,
            "latency": latency_ms
        }

    def build_candles_in_memory(self, tick: Any, bid: float, ask: float):
        """Continuously builds/updates the candles thread-safely based on ticks."""
        settings = get_settings()
        exec_tf_str = settings.get("timeframes", {}).get("execution", "M1")
        _, tf_duration = self.tf_map.get(exec_tf_str, (mt5.TIMEFRAME_M1, 60))
        
        # Calculate price as mid price
        price = (bid + ask) / 2.0
        tick_time = tick.time
        bar_time = tick_time - (tick_time % tf_duration)
        
        state.lock.acquire()
        candles = state.candles
        
        if len(candles) == 0:
            # Initialize first candle if empty
            candles.append({
                "time": bar_time,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "tick_volume": 1
            })
            self.logger.info("Initializing empty candle list with first live bar.")
        else:
            last_candle = candles[-1]
            if last_candle["time"] == bar_time:
                # Update current bar
                last_candle["high"] = max(last_candle["high"], price)
                last_candle["low"] = min(last_candle["low"], price)
                last_candle["close"] = price
                last_candle["tick_volume"] += 1
            elif bar_time > last_candle["time"]:
                # New bar started: sync final value of previous candle from MT5 to ensure perfect sync
                # and open new candle
                try:
                    state.lock.release() # Release to fetch rates safely
                    self.sync_previous_candle(last_candle["time"], exec_tf_str)
                    state.lock.acquire()
                except Exception as e:
                    self.logger.error(f"Sync previous candle failed: {e}")
                    
                candles.append({
                    "time": bar_time,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "tick_volume": 1
                })
                # Evict old candles to keep size optimized (max 500)
                if len(candles) > 500:
                    candles.pop(0)
                    
                self.logger.info(f"New {exec_tf_str} candle started at time {datetime.fromtimestamp(bar_time, timezone.utc).strftime('%H:%M:%S')} UTC.")
                
        state.lock.release()

    def sync_previous_candle(self, last_bar_time: int, exec_tf_str: str):
        """Helper to fetch completed copy_rates bar to align historical candle volume/highs/lows."""
        mt5_tf, _ = self.tf_map.get(exec_tf_str, (mt5.TIMEFRAME_M1, 60))
        rates = mt5.copy_rates_from(self.symbol, mt5_tf, last_bar_time, 1)
        if rates is not None and len(rates) > 0:
            r = rates[0]
            if r[0] == last_bar_time:
                state.lock.acquire()
                candles = state.candles
                for idx in range(len(candles)-1, -1, -1):
                    if candles[idx]["time"] == last_bar_time:
                        candles[idx]["open"] = float(r[1])
                        candles[idx]["high"] = float(r[2])
                        candles[idx]["low"] = float(r[3])
                        candles[idx]["close"] = float(r[4])
                        candles[idx]["tick_volume"] = int(r[5])
                        break
                state.lock.release()
