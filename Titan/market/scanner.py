import numpy as np
import logging
import json
import sqlite3
from datetime import datetime, timezone
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, DB_PATH
from Titan.market.sessions import SessionManager
from Titan.market.economic_calendar import EconomicCalendar
from Titan.strategies.technical_analysis import TechAnalysis

logger = logging.getLogger("Titan.MultiTimeframeScanner")

class MultiTimeframeScanner:
    
    @staticmethod
    def calculate_ema(prices, period=20):
        if len(prices) < period:
            return 0.0
        prices_arr = np.array(prices, dtype=float)
        alpha = 2.0 / (period + 1.0)
        ema = prices_arr[0]
        for p in prices_arr[1:]:
            ema = alpha * p + (1.0 - alpha) * ema
        return float(ema)
        
    @staticmethod
    def calculate_atr(highs, lows, closes, period=14):
        if len(closes) < period + 1:
            return 0.0
        highs = np.array(highs, dtype=float)
        lows = np.array(lows, dtype=float)
        closes = np.array(closes, dtype=float)
        
        tr_list = []
        for i in range(1, len(closes)):
            tr1 = highs[i] - lows[i]
            tr2 = abs(highs[i] - closes[i-1])
            tr3 = abs(lows[i] - closes[i-1])
            tr = max(tr1, tr2, tr3)
            tr_list.append(tr)
            
        atr = tr_list[0]
        # Wilders EMA smoothing for ATR
        alpha = 1.0 / period
        for tr in tr_list[1:]:
            atr = alpha * tr + (1.0 - alpha) * atr
        return float(atr)

    @staticmethod
    def calculate_vwap(candles):
        """
        Calculates Volume Weighted Average Price (VWAP) for the session day.
        Restarts cumulative counts on a daily change.
        """
        # For simplicity in scanning window, we compute running VWAP on the buffer
        cum_volume_price = 0.0
        cum_volume = 0.0
        
        for c in candles:
            typical_price = (c["high"] + c["low"] + c["close"]) / 3.0
            volume = float(max(1, c["tick_volume"]))
            cum_volume_price += typical_price * volume
            cum_volume += volume
            
        if cum_volume == 0:
            return candles[-1]["close"]
        return cum_volume_price / cum_volume

    @staticmethod
    def calculate_rsi(closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        closes = np.array(closes, dtype=float)
        diff = np.diff(closes)
        gains = np.where(diff > 0, diff, 0.0)
        losses = np.where(diff < 0, -diff, 0.0)
        
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        if avg_loss == 0:
            return 100.0
            
        for i in range(period, len(diff)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
            
        if avg_loss == 0:
            return 100.0
            
        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))

    @staticmethod
    def calculate_macd(closes, fast=12, slow=26, signal=9):
        if len(closes) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
            
        closes_arr = np.array(closes, dtype=float)
        
        def get_ema_list(prices, p):
            ema = np.zeros_like(prices)
            ema[0] = prices[0]
            alpha = 2.0 / (p + 1.0)
            for idx in range(1, len(prices)):
                ema[idx] = alpha * prices[idx] + (1.0 - alpha) * ema[idx-1]
            return ema
            
        ema_fast = get_ema_list(closes_arr, fast)
        ema_slow = get_ema_list(closes_arr, slow)
        
        macd_line = ema_fast - ema_slow
        signal_line = get_ema_list(macd_line, signal)
        histogram = macd_line - signal_line
        
        return {
            "macd": float(macd_line[-1]),
            "signal": float(signal_line[-1]),
            "histogram": float(histogram[-1])
        }

    @staticmethod
    def calculate_bollinger_bands(closes, period=20, num_std=2):
        if len(closes) < period:
            return {"middle": closes[-1], "upper": closes[-1], "lower": closes[-1]}
        closes_slice = np.array(closes[-period:], dtype=float)
        middle = float(np.mean(closes_slice))
        std = float(np.std(closes_slice))
        return {
            "middle": middle,
            "upper": middle + (num_std * std),
            "lower": middle - (num_std * std)
        }

    @staticmethod
    def calculate_adx(highs, lows, closes, period=14):
        if len(closes) < (period * 2):
            return 25.0 # default balance
            
        highs = np.array(highs, dtype=float)
        lows = np.array(lows, dtype=float)
        closes = np.array(closes, dtype=float)
        
        upmoves = highs[1:] - highs[:-1]
        downmoves = lows[:-1] - lows[1:]
        
        plus_dm = np.where((upmoves > downmoves) & (upmoves > 0), upmoves, 0.0)
        minus_dm = np.where((downmoves > upmoves) & (downmoves > 0), downmoves, 0.0)
        
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            tr_list.append(tr)
            
        tr_arr = np.array(tr_list)
        
        # Exponential smoothing (Wilder's)
        tr_smooth = np.zeros(len(tr_arr) - period + 1)
        plus_dm_smooth = np.zeros(len(plus_dm) - period + 1)
        minus_dm_smooth = np.zeros(len(minus_dm) - period + 1)
        
        tr_smooth[0] = np.sum(tr_arr[:period])
        plus_dm_smooth[0] = np.sum(plus_dm[:period])
        minus_dm_smooth[0] = np.sum(minus_dm[:period])
        
        for idx in range(1, len(tr_smooth)):
            tr_smooth[idx] = tr_smooth[idx-1] - (tr_smooth[idx-1]/period) + tr_arr[period - 1 + idx]
            plus_dm_smooth[idx] = plus_dm_smooth[idx-1] - (plus_dm_smooth[idx-1]/period) + plus_dm[period - 1 + idx]
            minus_dm_smooth[idx] = minus_dm_smooth[idx-1] - (minus_dm_smooth[idx-1]/period) + minus_dm[period - 1 + idx]
            
        # Avoid division by zero
        tr_smooth = np.where(tr_smooth == 0, 0.0001, tr_smooth)
        
        plus_di = 100 * (plus_dm_smooth / tr_smooth)
        minus_di = 100 * (minus_dm_smooth / tr_smooth)
        
        di_sum = plus_di + minus_di
        di_sum = np.where(di_sum == 0, 0.0001, di_sum)
        dx = 100 * (np.abs(plus_di - minus_di) / di_sum)
        
        adx = np.zeros(len(dx) - period + 1)
        adx[0] = np.mean(dx[:period])
        for idx in range(1, len(adx)):
            adx[idx] = (adx[idx-1] * (period - 1) + dx[period - 1 + idx]) / period
            
        return float(adx[-1])

    @staticmethod
    def identify_swings_and_structure(candles):
        """
        Calculates swing points, order blocks, FVG and break of structure details.
        Leverages logic from the confluence indicators calculation module.
        """
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        # Identify Swings
        swing_highs = []
        swing_lows = []
        for i in range(2, len(candles) - 2):
            if highs[i] == max(highs[i-2:i+3]):
                swing_highs.append({"index": i, "price": highs[i], "time": candles[i]["time"]})
            if lows[i] == min(lows[i-2:i+3]):
                swing_lows.append({"index": i, "price": lows[i], "time": candles[i]["time"]})
                
        # Liquidity levels (uncleared swings)
        last_closes = closes[-5:]
        liquidity_zones = []
        for sh in swing_highs[-3:]:
            if max(last_closes) < sh["price"]:
                liquidity_zones.append({"type": "BUY_SIDE", "price": sh["price"]})
        for sl in swing_lows[-3:]:
            if min(last_closes) > sl["price"]:
                liquidity_zones.append({"type": "SELL_SIDE", "price": sl["price"]})
                
        # Fair Value Gaps (FVG)
        fvg_zones = []
        for i in range(1, len(candles) - 1):
            # Bullish FVG: Low of candle i+1 is higher than High of candle i-1
            if candles[i+1]["low"] > candles[i-1]["high"]:
                fvg_zones.append({
                    "type": "BULLISH",
                    "top": candles[i+1]["low"],
                    "bottom": candles[i-1]["high"],
                    "time": candles[i]["time"]
                })
            # Bearish FVG: High of candle i+1 is lower than Low of candle i-1
            elif candles[i+1]["high"] < candles[i-1]["low"]:
                fvg_zones.append({
                    "type": "BEARISH",
                    "top": candles[i-1]["low"],
                    "bottom": candles[i+1]["high"],
                    "time": candles[i]["time"]
                })

        # Order Blocks (OB)
        # Standard definition: last down candle before up move (bullish OB), last up before down (bearish)
        order_blocks = []
        for i in range(2, len(candles) - 2):
            # Bullish
            if candles[i-1]["close"] < candles[i-1]["open"] and candles[i]["close"] > candles[i]["open"] and candles[i+1]["close"] > candles[i]["close"]:
                order_blocks.append({
                    "type": "BULLISH",
                    "top": max(candles[i-1]["open"], candles[i-1]["high"]),
                    "bottom": candles[i-1]["low"],
                    "price": candles[i-1]["low"],
                    "time": candles[i-1]["time"]
                })
            # Bearish
            elif candles[i-1]["close"] > candles[i-1]["open"] and candles[i]["close"] < candles[i]["open"] and candles[i+1]["close"] < candles[i]["close"]:
                order_blocks.append({
                    "type": "BEARISH",
                    "top": candles[i-1]["high"],
                    "bottom": min(candles[i-1]["open"], candles[i-1]["low"]),
                    "price": candles[i-1]["high"],
                    "time": candles[i-1]["time"]
                })

        # Market Structure (BOS / CHoCH)
        # Simply evaluate structural shift in last 10 candles
        bos_bullish = False
        bos_bearish = False
        choch_bullish = False
        choch_bearish = False
        
        if len(swing_highs) > 2 and len(swing_lows) > 2:
            last_sh = swing_highs[-1]["price"]
            prev_sh = swing_highs[-2]["price"]
            last_sl = swing_lows[-1]["price"]
            prev_sl = swing_lows[-2]["price"]
            
            # Break of structure: new High breaks prior High (in trend direction)
            if closes[-1] > last_sh:
                bos_bullish = True
            elif closes[-1] < last_sl:
                bos_bearish = True
                
            # Change of Character: first counter trend structural break
            if last_closes[-1] > prev_sh and last_closes[-2] <= prev_sh:
                choch_bullish = True
            elif last_closes[-1] < prev_sl and last_closes[-2] >= prev_sl:
                choch_bearish = True

        return {
            "swings": {
                "highs": [{"price": s["price"], "time": s["time"]} for s in swing_highs[-3:]],
                "lows": [{"price": s["price"], "time": s["time"]} for s in swing_lows[-3:]]
            },
            "liquidity": liquidity_zones[-3:],
            "fvg": fvg_zones[-3:],
            "order_blocks": order_blocks[-3:],
            "bos": {"bullish": bos_bullish, "bearish": bos_bearish},
            "choch": {"bullish": choch_bullish, "bearish": choch_bearish}
        }

    @classmethod
    def scan_timeframe(cls, symbol, mt5_timeframe, timeframe_label, count=100):
        """
        Runs full scans on a specific timeframe and saves outcomes to SQLite database.
        """
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
        if rates is None or len(rates) == 0:
            logger.error(f"Scanner failed to fetch rates for {symbol} ({timeframe_label})")
            return None
            
        candles = []
        for r in rates:
            candles.append({
                "time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "tick_volume": int(r[5]),
                "spread": int(r[6]),
                "real_volume": int(r[7])
            })
            
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        # Calculate Indicators
        ema_20 = cls.calculate_ema(closes, 20)
        atr_14 = cls.calculate_atr(highs, lows, closes, 14)
        rsi_14 = cls.calculate_rsi(closes, 14)
        vwap = cls.calculate_vwap(candles)
        adx = cls.calculate_adx(highs, lows, closes, 14)
        macd = cls.calculate_macd(closes)
        bb = cls.calculate_bollinger_bands(closes)
        
        # Trend
        last_close = closes[-1]
        trend = "BULLISH" if last_close > ema_20 else "BEARISH"
        
        # Volatility
        volatility_ratio = atr_14 / (last_close * 0.001) if last_close > 0 else 1.0
        volatility = "HIGH" if volatility_ratio > 1.5 else "LOW" if volatility_ratio < 0.7 else "NORMAL"
        
        # Structure calculations
        struct = cls.identify_swings_and_structure(candles)
        
        # Session info
        session_info = SessionManager.get_current_sessions()
        session_active = session_info["session_desc"]
        
        # SQL logging
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            ohlc_data = json.dumps({"open": candles[-1]["open"], "high": candles[-1]["high"], "low": candles[-1]["low"], "close": candles[-1]["close"], "volume": candles[-1]["tick_volume"]})
            indicators = json.dumps({
                "ema_20": ema_20,
                "atr_14": atr_14,
                "rsi_14": rsi_14,
                "vwap": vwap,
                "adx": adx,
                "macd": macd,
                "bollinger": bb
            })
            structure = json.dumps(struct)
            
            cursor.execute(
                """
                INSERT INTO scanner_history (symbol, timeframe, ohlc_data, indicators_json, structure_json, trend, volatility, session)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol, timeframe_label, ohlc_data, indicators, structure, trend, volatility, session_active)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to persist scanner log: {e}")
            
        return {
            "symbol": symbol,
            "timeframe": timeframe_label,
            "ohlc": candles[-1],
            "indicators": {
                "ema_20": ema_20,
                "atr_14": atr_14,
                "rsi_14": rsi_14,
                "vwap": vwap,
                "adx": adx,
                "macd": macd,
                "bollinger": bb
            },
            "structure": struct,
            "trend": trend,
            "volatility": volatility,
            "session": session_active
        }
