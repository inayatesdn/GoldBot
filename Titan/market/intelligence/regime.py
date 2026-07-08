import numpy as np
from typing import List, Dict, Any
from Titan.market.intelligence.utils import calculate_ema, calculate_atr, calculate_adx

class MarketRegimeEngine:
    """
    Classifies market regime into Trending, Strong Trend, Weak Trend, Range, 
    Compression, Expansion, Reversal, Accumulation, Distribution, or News Spike.
    Returns confidence, reason, state, and metrics.
    """
    
    @staticmethod
    def classify(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(candles) < 30:
            return {
                "confidence": 0.5,
                "reason": "Insufficient candle buffer size (< 30)",
                "state": "Range",
                "metrics": {}
            }
            
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        # 1. Fetch Key Indicators
        atr_14 = calculate_atr(highs, lows, closes, 14)
        prev_atr_14 = calculate_atr(highs[:-5], lows[:-5], closes[:-5], 14)
        adx_14 = calculate_adx(highs, lows, closes, 14)
        
        # 2. Check EMA slope
        ema_20 = calculate_ema(closes, 20)
        ema_50 = calculate_ema(closes, 50)
        
        ema_20_slope = (ema_20[-1] - ema_20[-5]) / 5.0
        ema_aligned_bull = ema_20[-1] > ema_50[-1] and ema_20[-5] > ema_50[-5]
        ema_aligned_bear = ema_20[-1] < ema_50[-1] and ema_20[-5] < ema_50[-5]
        
        # 3. Swing High/Low Structure
        swing_span = 5
        swing_highs = []
        swing_lows = []
        for i in range(swing_span, len(candles) - swing_span):
            if highs[i] == max(highs[i-swing_span : i+swing_span+1]):
                swing_highs.append(highs[i])
            if lows[i] == min(lows[i-swing_span : i+swing_span+1]):
                swing_lows.append(lows[i])
                
        # 4. Volatility & Ranges
        volatility = atr_14 / (closes[-1] * 0.001) if closes[-1] > 0 else 1.0
        recent_max = max(closes[-20:])
        recent_min = min(closes[-20:])
        range_pct = (recent_max - recent_min) / closes[-1] if closes[-1] > 0 else 0.0
        
        # News Spike detection (single bar size is > 3 * ATR_14)
        last_bar_range = abs(candles[-1]["close"] - candles[-1]["open"])
        is_news_spike = (last_bar_range > 3.5 * atr_14) if atr_14 > 0 else False
        
        # Determine Regime State
        state = "Range"
        confidence = 0.5
        reason = "Market is in range compression."
        
        # Compression (Bollinger Band / ATR narrow shrinkage)
        is_compression = (atr_14 < prev_atr_14 * 0.70)
        is_expansion = (atr_14 > prev_atr_14 * 1.35)
        
        if is_news_spike:
            state = "News Spike"
            confidence = 0.90
            reason = f"Massive volatility expansion on last bar ({last_bar_range:.2f} pts vs ATR {atr_14:.2f})"
        elif is_compression and adx_14 < 20:
            state = "Compression"
            confidence = 0.80
            reason = "Volatility is shrinking dramatically under ADX threshold"
        elif is_expansion and adx_14 > 25:
            state = "Expansion"
            confidence = 0.85
            reason = "Volatility ATR expansion observed alongside rising trend strength"
        elif adx_14 > 40:
            state = "Strong Trend"
            confidence = 0.92
            direction = "BULLISH" if ema_20_slope > 0 else "BEARISH"
            reason = f"Institutional strong {direction} trend (ADX {adx_14:.1f}, EMA Slope {ema_20_slope:.4f})"
        elif adx_14 > 25 and (ema_aligned_bull or ema_aligned_bear):
            state = "Trending"
            confidence = 0.82
            direction = "BULLISH" if ema_aligned_bull else "BEARISH"
            reason = f"Trending market alignment confirmed ({direction})"
        elif adx_14 < 15 and range_pct < 0.002:
            # Low volatility accumulation close to swing lows
            if len(swing_lows) > 0 and abs(closes[-1] - swing_lows[-1]) < (atr_14 * 1.5):
                state = "Accumulation"
                confidence = 0.75
                reason = "Flat consolidation near major support level (Accumulation cycle)"
            elif len(swing_highs) > 0 and abs(closes[-1] - swing_highs[-1]) < (atr_14 * 1.5):
                state = "Distribution"
                confidence = 0.75
                reason = "Flat consolidation near prior resistance peaks (Distribution zone)"
        elif adx_14 >= 20 and adx_14 <= 25:
            state = "Weak Trend"
            confidence = 0.70
            reason = f"Trend showing signs of exhaustion/weakness (ADX {adx_14:.1f})"
        elif len(closes) > 10 and ((closes[-1] > ema_20[-1] and closes[-3] < ema_20[-3]) or (closes[-1] < ema_20[-1] and closes[-3] > ema_20[-3])):
            state = "Reversal"
            confidence = 0.65
            reason = "Price crossing main short term EMA trend line suggesting structural shift"
            
        metrics = {
            "atr_14": atr_14,
            "adx_14": adx_14,
            "ema_20_slope": ema_20_slope,
            "range_pct": range_pct,
            "volatility_ratio": volatility,
            "is_compression": bool(is_compression),
            "is_expansion": bool(is_expansion)
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
