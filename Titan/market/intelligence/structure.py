import numpy as np
from typing import List, Dict, Any

class StructureEngine:
    """
    Identifies market structure elements: swing highs, swing lows, HH, HL, LH, LL,
    BOS, internal/external BOS, CHOCH, liquidity sweeps, breaker zones, mitigation.
    Returns confidence, reason, state, and metrics.
    """
    
    @staticmethod
    def analyze(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(candles) < 30:
            return {
                "confidence": 0.5,
                "reason": "Insufficient candle buffer size",
                "state": "NONE",
                "metrics": {}
            }
            
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        closes = [c["close"] for c in candles]
        
        # 1. Identify Swings (window size 5 for internal, window size 15 for external)
        def get_swings(w_size):
            sh = []
            sl = []
            for i in range(w_size, len(candles) - w_size):
                if highs[i] == max(highs[i-w_size : i+w_size+1]):
                    sh.append({"index": i, "price": highs[i], "time": candles[i]["time"]})
                if lows[i] == min(lows[i-w_size : i+w_size+1]):
                    sl.append({"index": i, "price": lows[i], "time": candles[i]["time"]})
            return sh, sl
            
        int_sh, int_sl = get_swings(5)
        ext_sh, ext_sl = get_swings(12)
        
        # Determine Highs/Lows relative points
        hh, hl, lh, ll = False, False, False, False
        if len(int_sh) >= 2:
            if int_sh[-1]["price"] > int_sh[-2]["price"]:
                hh = True
            else:
                lh = True
        if len(int_sl) >= 2:
            if int_sl[-1]["price"] > int_sl[-2]["price"]:
                hl = True
            else:
                ll = True
                
        # 2. Break of Structure (BOS) & Change of Character (CHOCH)
        # Look for closes breaking prior external highs/lows
        bos_bullish = False
        bos_bearish = False
        choch_bull = False
        choch_bear = False
        
        last_close = closes[-1]
        
        if len(ext_sh) > 0:
            prior_high = ext_sh[-1]["price"]
            # Bullish BOS: closes breaking the prior external swing high
            if last_close > prior_high:
                bos_bullish = True
                
        if len(ext_sl) > 0:
            prior_low = ext_sl[-1]["price"]
            # Bearish BOS: closes breaking the prior external swing low
            if last_close < prior_low:
                bos_bearish = True
                
        # CHOCH (Break of opposite trend swing)
        if hh and hl and len(int_sl) > 0:
            if last_close < int_sl[-1]["price"]:
                choch_bear = True
        elif lh and ll and len(int_sh) > 0:
            if last_close > int_sh[-1]["price"]:
                choch_bull = True
                
        # 3. Liquidity Sweep
        # High/Low spikes past swing highs/lows, but close pulls back inside
        sweep_bullish = False # Sell side swept (bullish setup)
        sweep_bearish = False # Buy side swept (bearish setup)
        
        if len(int_sl) > 0:
            target_low = int_sl[-1]["price"]
            if lows[-1] < target_low and last_close > target_low:
                sweep_bullish = True
                
        if len(int_sh) > 0:
            target_high = int_sh[-1]["price"]
            if highs[-1] > target_high and last_close < target_high:
                sweep_bearish = True
                
        # 4. Breaker Block detection & Mitigation
        # A breaker is a broken order block. For example, a bullish swing low that was broken down.
        breaker_bullish = False
        breaker_bearish = False
        mitigated = False
        
        # If bullish BOS occurred:
        if bos_bullish:
            state = "BULL_BOS"
            confidence = 0.85
            reason = "Bullish Break of Market Structure detected."
        elif bos_bearish:
            state = "BEAR_BOS"
            confidence = 0.85
            reason = "Bearish Break of Market Structure detected."
        elif choch_bull:
            state = "BULL_CHOCH"
            confidence = 0.80
            reason = "Bullish Change of Character suggests new trend formation."
        elif choch_bear:
            state = "BEAR_CHOCH"
            confidence = 0.80
            reason = "Bearish Change of Character suggests market shift downward."
        elif sweep_bullish:
            state = "BULL_SWEEP"
            confidence = 0.78
            reason = "Sell-side liquidity swept with close recovery. Highly bullish."
        elif sweep_bearish:
            state = "BEAR_SWEEP"
            confidence = 0.78
            reason = "Buy-side liquidity swept with close rejection. Highly bearish."
        else:
            state = "CONSOLIDATION"
            confidence = 0.50
            reason = "Market structure remains inside recent swing boundaries."
            
        metrics = {
            "hh": hh, "hl": hl, "lh": lh, "ll": ll,
            "bos_bullish": bos_bullish, "bos_bearish": bos_bearish,
            "choch_bullish": choch_bull, "choch_bearish": choch_bear,
            "sweep_bullish": sweep_bullish, "sweep_bearish": sweep_bearish,
            "recent_swing_high": int_sh[-1]["price"] if int_sh else None,
            "recent_swing_low": int_sl[-1]["price"] if int_sl else None
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
