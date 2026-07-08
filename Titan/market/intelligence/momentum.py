import numpy as np
from typing import List, Dict, Any
from Titan.market.intelligence.utils import calculate_rsi, calculate_macd, calculate_atr

class MomentumEngine:
    """
    Computes RSI, MACD, ROC (Rate of Change), Momentum Strength,
    Acceleration/Deceleration, and detects Momentum Divergences.
    Returns confidence, reason, state, and metrics.
    """
    
    @staticmethod
    def analyze(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(candles) < 30:
            return {
                "confidence": 0.5,
                "reason": "Insufficient candles",
                "state": "NONE",
                "metrics": {}
            }
            
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        # 1. Indicator calculations
        rsi_list = calculate_rsi(closes, 14)
        macd_res = calculate_macd(closes, 12, 26, 9)
        atr_14 = calculate_atr(highs, lows, closes, 14)
        
        rsi_val = rsi_list[-1]
        
        # ROC (Rate of Change)
        roc = 0.0
        if len(closes) > 10 and closes[-10] > 0:
            roc = (closes[-1] - closes[-10]) / closes[-10] * 100.0
            
        # Acceleration / Deceleration
        # Delta of ROC
        prev_roc = 0.0
        if len(closes) > 13 and closes[-13] > 0:
            prev_roc = (closes[-4] - closes[-13]) / closes[-13] * 100.0
        acceleration = roc - prev_roc
        
        # Determing Momentum Direction & Strength
        strength = "NEUTRAL"
        if rsi_val > 70:
            strength = "OVERBOUGHT"
        elif rsi_val < 30:
            strength = "OVERSOLD"
        elif rsi_val > 55:
            strength = "STRONG_BULLISH"
        elif rsi_val < 45:
            strength = "STRONG_BEARISH"
            
        # 2. Bullish / Bearish Divergence Detection
        # Check matching local peaks
        bull_divergence = False
        bear_divergence = False
        
        # Detect last two local swing lows in price
        price_lows = []
        rsi_lows = []
        for i in range(len(closes) - 15, len(closes) - 2):
            if closes[i] == min(closes[i-2 : i+3]):
                price_lows.append((closes[i], i))
                rsi_lows.append((rsi_list[i], i))
                
        if len(price_lows) >= 2:
            # Bullish divergence: lower low in price but higher low in RSI
            if price_lows[-1][0] < price_lows[-2][0] and rsi_lows[-1][0] > rsi_lows[-2][0]:
                bull_divergence = True
                
        price_highs = []
        rsi_highs = []
        for i in range(len(closes) - 15, len(closes) - 2):
            if closes[i] == max(closes[i-2 : i+3]):
                price_highs.append((closes[i], i))
                rsi_highs.append((rsi_list[i], i))
                
        if len(price_highs) >= 2:
            # Bearish divergence: higher high in price but lower high in RSI
            if price_highs[-1][0] > price_highs[-2][0] and rsi_highs[-1][0] < rsi_highs[-2][0]:
                bear_divergence = True
                
        # Form Confidence & Reason
        confidence = 0.55
        state = "NEUTRAL"
        reason = f"Momentum indexes are balanced. RSI: {rsi_val:.1f}"
        
        if bull_divergence:
            state = "BULL_DIVERGENT"
            confidence = 0.88
            reason = f"Strong Bullish RSI Divergence: Classifying price reversal trigger."
        elif bear_divergence:
            state = "BEAR_DIVERGENT"
            confidence = 0.88
            reason = f"Strong Bearish RSI Divergence: Classifying price reversal trigger."
        elif macd_res["histogram"] > 0 and rsi_val > 55 and acceleration > 0:
            state = "BULL_ACCELERATING"
            confidence = 0.80
            reason = f"Momentum accelerating upwards (RSI: {rsi_val:.1f}, ROC: {roc:.3f})"
        elif macd_res["histogram"] < 0 and rsi_val < 45 and acceleration < 0:
            state = "BEAR_ACCELERATING"
            confidence = 0.80
            reason = f"Momentum accelerating downwards (RSI: {rsi_val:.1f}, ROC: {roc:.3f})"
        elif strength == "OVERBOUGHT":
            state = "OVERBOUGHT"
            confidence = 0.70
            reason = "RSI index suggests overbought conditions (>70)."
        elif strength == "OVERSOLD":
            state = "OVERSOLD"
            confidence = 0.70
            reason = "RSI index suggests oversold conditions (<30)."
            
        metrics = {
            "rsi": rsi_val,
            "macd": macd_res,
            "roc": roc,
            "acceleration": acceleration,
            "atr": atr_14,
            "bull_divergence": bull_divergence,
            "bear_divergence": bear_divergence,
            "strength": strength
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
