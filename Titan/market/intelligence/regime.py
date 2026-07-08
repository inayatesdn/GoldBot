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
        
        # Determine Regime State conform to checklist (Step 5)
        state_val = "Range"
        confidence = 0.5
        reason = "Market is in range consolidation."
        
        # Check compression vs expansion
        is_compression = (atr_14 < prev_atr_14 * 0.70)
        is_expansion = (atr_14 > prev_atr_14 * 1.35)
        
        if volatility > 1.8:
            state_val = "High Volatility"
            confidence = 0.90
            reason = f"High Volatility regime detected (index score: {volatility:.2f})"
        elif volatility < 0.6:
            state_val = "Low Volatility"
            confidence = 0.85
            reason = f"Low Volatility compression detected (index score: {volatility:.2f})"
        elif adx_14 > 30 and ema_aligned_bull:
            state_val = "Strong Bull Trend"
            confidence = 0.95
            reason = f"Strong Bullish Trend confirmed (ADX: {adx_14:.1f}, EMA Slope: {ema_20_slope:.4f})"
        elif adx_14 > 30 and ema_aligned_bear:
            state_val = "Strong Bear Trend"
            confidence = 0.95
            reason = f"Strong Bearish Trend confirmed (ADX: {adx_14:.1f}, EMA Slope: {ema_20_slope:.4f})"
        elif adx_14 > 25 and (atr_14 > prev_atr_14 * 1.35):
            state_val = "Breakout"
            confidence = 0.80
            reason = f"Breakout state detected (ATR expansion ratio: {(atr_14/prev_atr_14) if prev_atr_14 > 0 else 1.0:.2f})"
        elif len(closes) > 10 and ((closes[-1] > ema_20[-1] and closes[-3] < ema_20[-3]) or (closes[-1] < ema_20[-1] and closes[-3] > ema_20[-3])):
            state_val = "Reversal"
            confidence = 0.75
            reason = "Structural Reversal crossing main short term EMA line."
        else:
            state_val = "Range"
            confidence = 0.70
            reason = f"Standard range consolidation detected (ADX: {adx_14:.1f})"
            
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
            "state": state_val,
            "metrics": metrics
        }
