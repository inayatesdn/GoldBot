import numpy as np
import logging

logger = logging.getLogger("Titan.MarketRegime")

class RegimeClassifier:
    
    @staticmethod
    def calculate_ema(prices, period):
        """Calculates Exponential Moving Average."""
        if len(prices) < period:
            return np.array([np.mean(prices)] * len(prices))
        
        alpha = 2.0 / (period + 1.0)
        ema = np.zeros_like(prices)
        ema[0] = np.mean(prices[:period])
        
        for i in range(1, len(prices)):
            ema[i] = alpha * prices[i] + (1 - alpha) * ema[i - 1]
            
        return ema

    @staticmethod
    def calculate_atr(highs, lows, closes, period=14):
        """Calculates Average True Range."""
        if len(closes) < period + 1:
            return np.ones_like(closes) * 0.1
            
        tr = np.zeros_like(closes)
        for i in range(1, len(closes)):
            h_l = highs[i] - lows[i]
            h_pc = abs(highs[i] - closes[i - 1])
            l_pc = abs(lows[i] - closes[i - 1])
            tr[i] = max(h_l, h_pc, l_pc)
            
        atr = np.zeros_like(closes)
        atr[period] = np.mean(tr[1:period+1])
        
        for i in range(period + 1, len(closes)):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
            
        return atr

    @staticmethod
    def classify_market_state(candles_m5, candles_h1, current_spread_points=10):
        """
        Analyzes M5 and H1 candles to classify the market state.
        candles_m5 & candles_h1 are list of dicts with: open, high, low, close, volume, time.
        """
        if len(candles_m5) < 30 or len(candles_h1) < 30:
            return {
                "regime": "No Trade",
                "direction": "SIDEWAYS",
                "volatility": "LOW",
                "reason": "Insufficient data (minimum 30 candles required)",
                "atr": 0.0,
                "spread_pct": 0.0
            }
            
        closes_m5 = np.array([c["close"] for c in candles_m5], dtype=float)
        highs_m5 = np.array([c["high"] for c in candles_m5], dtype=float)
        lows_m5 = np.array([c["low"] for c in candles_m5], dtype=float)
        volumes_m5 = np.array([c["real_volume"] if c.get("real_volume") else c.get("tick_volume", 1) for c in candles_m5], dtype=float)
        
        closes_h1 = np.array([c["close"] for c in candles_h1], dtype=float)
        highs_h1 = np.array([c["high"] for c in candles_h1], dtype=float)
        lows_h1 = np.array([c["low"] for c in candles_h1], dtype=float)
        
        # Calculate Indicators on M5
        ema20_m5 = RegimeClassifier.calculate_ema(closes_m5, 20)
        ema50_m5 = RegimeClassifier.calculate_ema(closes_m5, 50)
        ema200_m5 = RegimeClassifier.calculate_ema(closes_m5, 200)
        atr14_m5 = RegimeClassifier.calculate_atr(highs_m5, lows_m5, closes_m5, 14)
        
        # Calculate Indicators on H1
        ema20_h1 = RegimeClassifier.calculate_ema(closes_h1, 20)
        ema50_h1 = RegimeClassifier.calculate_ema(closes_h1, 50)
        
        # Current Metrics
        curr_close_m5 = closes_m5[-1]
        curr_atr = atr14_m5[-1]
        avg_atr = np.mean(atr14_m5[-20:])
        avg_vol = np.mean(volumes_m5[-20:])
        curr_vol = volumes_m5[-1]
        
        # Trend directions on H1 and M5
        h1_bullish = ema20_h1[-1] > ema50_h1[-1]
        h1_bearish = ema20_h1[-1] < ema50_h1[-1]
        
        m5_bullish = ema20_m5[-1] > ema50_m5[-1] > ema200_m5[-1]
        m5_bearish = ema20_m5[-1] < ema50_m5[-1] < ema200_m5[-1]
        
        # Volatility Classification
        volatility_state = "NORMAL"
        if curr_atr > avg_atr * 1.8:
            volatility_state = "HIGH"
        elif curr_atr < avg_atr * 0.5:
            volatility_state = "LOW"
            
        # 1. Low Liquidity Check
        # Check if spread is too wide or volume is dead
        if (current_spread_points > 120) or (curr_vol < avg_vol * 0.15 and volatility_state == "LOW"):
            return {
                "regime": "Low Liquidity",
                "direction": "SIDEWAYS",
                "volatility": volatility_state,
                "reason": f"Spread ({current_spread_points} pts) or Volume ({curr_vol:.0f}/{avg_vol:.0f}) indicates low liquidity.",
                "atr": curr_atr,
                "spread": current_spread_points
            }
            
        # 2. High Volatility / News Driven Check
        if volatility_state == "HIGH" and curr_vol > avg_vol * 1.5:
            return {
                "regime": "News Driven",
                "direction": "BULLISH" if curr_close_m5 > ema20_m5[-1] else "BEARISH",
                "volatility": "HIGH",
                "reason": "Sudden volume spike combined with extreme ATR volatility.",
                "atr": curr_atr,
                "spread": current_spread_points
            }
            
        # 3. Manipulation (Liquidity Sweep Reversal check)
        # Search last 3 bars for a sweep: high/low pushes past standard bounds but closes inside
        # Check standard deviations of extremes
        recent_highs = highs_m5[-3:]
        recent_lows = lows_m5[-3:]
        recent_closes = closes_m5[-3:]
        
        upper_band = ema20_m5[-1] + (2.0 * curr_atr)
        lower_band = ema20_m5[-1] - (2.0 * curr_atr)
        
        sweep_high = any(h > upper_band for h in recent_highs) and recent_closes[-1] < ema20_m5[-1]
        sweep_low = any(l < lower_band for l in recent_lows) and recent_closes[-1] > ema20_m5[-1]
        
        if (sweep_high or sweep_low) and volatility_state == "HIGH":
            return {
                "regime": "Manipulation",
                "direction": "BEARISH" if sweep_high else "BULLISH",
                "volatility": "HIGH",
                "reason": "Excursion outside target band rejected. Possible smart money sweep.",
                "atr": curr_atr,
                "spread": current_spread_points
            }

        # 4. Breakout Check
        # Current bar body is extraordinarily large and sweeps beyond standard deviation range
        body_size = abs(closes_m5[-1] - candles_m5[-1]["open"])
        if body_size > avg_atr * 1.5 and curr_vol > avg_vol * 1.2:
            return {
                "regime": "Breakout",
                "direction": "BULLISH" if closes_m5[-1] > candles_m5[-1]["open"] else "BEARISH",
                "volatility": "HIGH",
                "reason": "High-volume momentum candle breaking key range limit.",
                "atr": curr_atr,
                "spread": current_spread_points
            }

        # 5. Strong Trend vs Weak Trend
        if m5_bullish and h1_bullish:
            # Check slope
            slope = (ema20_m5[-1] - ema20_m5[-5]) / curr_atr
            if slope > 0.3:
                return {
                    "regime": "Strong Trend",
                    "direction": "BULLISH",
                    "volatility": volatility_state,
                    "reason": "Aligned M5/H1 bullish EMAs with significant positive slope.",
                    "atr": curr_atr,
                    "spread": current_spread_points
                }
            else:
                return {
                    "regime": "Weak Trend",
                    "direction": "BULLISH",
                    "volatility": volatility_state,
                    "reason": "Bullish alignment but lacking steep trending momentum.",
                    "atr": curr_atr,
                    "spread": current_spread_points
                }
                
        if m5_bearish and h1_bearish:
            slope = (ema20_m5[-1] - ema20_m5[-5]) / curr_atr
            if slope < -0.3:
                return {
                    "regime": "Strong Trend",
                    "direction": "BEARISH",
                    "volatility": volatility_state,
                    "reason": "Aligned M5/H1 bearish EMAs with significant negative slope.",
                    "atr": curr_atr,
                    "spread": current_spread_points
                }
            else:
                return {
                    "regime": "Weak Trend",
                    "direction": "BEARISH",
                    "volatility": volatility_state,
                    "reason": "Bearish alignment but lacking steep trending momentum.",
                    "atr": curr_atr,
                    "spread": current_spread_points
                }

        # 6. Reversal Check
        # Check crossover of EMA 20/50 on M5 representing a structural shift
        cross_bullish = ema20_m5[-1] > ema50_m5[-1] and ema20_m5[-2] <= ema50_m5[-2]
        cross_bearish = ema20_m5[-1] < ema50_m5[-1] and ema20_m5[-2] >= ema50_m5[-2]
        if (cross_bullish and h1_bullish) or (cross_bearish and h1_bearish):
            return {
                "regime": "Reversal",
                "direction": "BULLISH" if cross_bullish else "BEARISH",
                "volatility": volatility_state,
                "reason": "Recent EMA 20/50 crossing aligned with higher timeframe bias.",
                "atr": curr_atr,
                "spread": current_spread_points
            }
            
        # 7. Range / Accumulation / Distribution
        # EMAs are intersecting and flat
        flat_emas = abs(ema20_m5[-1] - ema50_m5[-1]) < (0.2 * curr_atr)
        if flat_emas:
            # Accumulation near consolidation support or distribution near resistance
            swing_high = np.max(closes_m5[-30:])
            swing_low = np.min(closes_m5[-30:])
            midpoint = (swing_high + swing_low) / 2
            
            if curr_close_m5 < swing_low + (0.25 * (swing_high - swing_low)):
                return {
                    "regime": "Accumulation",
                    "direction": "SIDEWAYS",
                    "volatility": volatility_state,
                    "reason": "Price consolidates in the bottom 25% of the 30-candle range.",
                    "atr": curr_atr,
                    "spread": current_spread_points
                }
            elif curr_close_m5 > swing_high - (0.25 * (swing_high - swing_low)):
                return {
                    "regime": "Distribution",
                    "direction": "SIDEWAYS",
                    "volatility": volatility_state,
                    "reason": "Price consolidates in the top 25% of the 30-candle range.",
                    "atr": curr_atr,
                    "spread": current_spread_points
                }
            else:
                return {
                    "regime": "Range",
                    "direction": "SIDEWAYS",
                    "volatility": volatility_state,
                    "reason": "Flat intersecting moving averages inside the consolidation core.",
                    "atr": curr_atr,
                    "spread": current_spread_points
                }
                
        # Fallback
        return {
            "regime": "Range",
            "direction": "SIDEWAYS",
            "volatility": volatility_state,
            "reason": "No strong directional or momentum signals, defaulting to Range state.",
            "atr": curr_atr,
            "spread": current_spread_points
        }
