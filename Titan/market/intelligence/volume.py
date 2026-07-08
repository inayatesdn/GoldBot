import numpy as np
from typing import List, Dict, Any

class VolumeEngine:
    """
    Computes Relative Volume (RVOL), Volume Spikes, Delta proxies, Buying/Selling pressure,
    Climax Volume, Absorption, and Exhaustion.
    Returns confidence, reason, state, and metrics.
    """
    
    @staticmethod
    def analyze(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(candles) < 21:
            return {
                "confidence": 0.5,
                "reason": "Insufficient candles",
                "state": "NONE",
                "metrics": {}
            }
            
        volumes = [float(max(1, c.get("tick_volume", 1))) for c in candles]
        closes = [c["close"] for c in candles]
        opens = [c["open"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        last_vol = volumes[-1]
        mean_vol = float(np.mean(volumes[-21:-1]))
        rvol = last_vol / mean_vol if mean_vol > 0 else 1.0
        
        # Volume Spike
        is_spike = rvol > 2.5
        
        # Buying & Selling Pressure
        # Using candle spread proportions to partition volume
        last_range = highs[-1] - lows[-1]
        bp = 0.0
        sp = 0.0
        if last_range > 0:
            if closes[-1] >= opens[-1]:
                # Bullish candle
                bp = last_vol * (closes[-1] - lows[-1]) / last_range
                sp = last_vol - bp
            else:
                # Bearish candle
                sp = last_vol * (highs[-1] - closes[-1]) / last_range
                bp = last_vol - sp
        else:
            bp = last_vol * 0.5
            sp = last_vol * 0.5
            
        # Delta Proxy
        delta_proxy = bp - sp
        
        # Climax, Absorption, Exhaustion
        is_climax = False
        is_absorption = False
        is_exhaustion = False
        
        avg_spread = np.mean([h - l for h, l in zip(highs[-20:-1], lows[-20:-1])])
        last_spread = highs[-1] - lows[-1]
        
        if rvol > 3.0 and last_spread > 2.0 * avg_spread:
            is_climax = True
        elif rvol > 2.0 and last_spread < 0.5 * avg_spread:
            # High volume, narrow range = Absorption
            is_absorption = True
        elif rvol < 0.40 and last_spread < 0.40 * avg_spread:
            # Low volume, low volatility = Exhaustion
            is_exhaustion = True
            
        # Form Confidence & Reason
        confidence = 0.55
        state = "NORMAL"
        reason = f"Volume levels are standard (Relative Vol: {rvol:.2f})."
        
        if is_climax:
            state = "VOLUME_CLIMAX"
            confidence = 0.82
            direction = "BULLISH" if delta_proxy > 0 else "BEARISH"
            reason = f"Extreme Climax volume detected ({direction} direction)."
        elif is_absorption:
            state = "ABSORPTION"
            confidence = 0.80
            reason = "Institutional absorption block orders detected. Large volume holding range."
        elif is_exhaustion:
            state = "EXHAUSTION"
            confidence = 0.65
            reason = "Trend exhaustion confirmed. Very lower participation."
        elif is_spike:
            state = "VOLUME_SPIKE"
            confidence = 0.75
            direction = "BUYING" if delta_proxy > 0 else "SELLING"
            reason = f"Volume spike detected with clear net {direction} pressure."
            
        metrics = {
            "rvol": rvol,
            "buying_pressure": bp,
            "selling_pressure": sp,
            "delta_proxy": delta_proxy,
            "is_spike": is_spike,
            "is_climax": is_climax,
            "is_absorption": is_absorption,
            "is_exhaustion": is_exhaustion
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
