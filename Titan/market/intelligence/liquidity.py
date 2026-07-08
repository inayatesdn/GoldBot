import numpy as np
from typing import List, Dict, Any

class LiquidityEngine:
    """
    Locates Equal Highs, Equal Lows, Liquidity Pools, Buy/Sell Side Liquidity,
    Stop Hunts, Liquidity Grabs, Liquidity Voids, and Resting Liquidity.
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
            
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        closes = [c["close"] for c in candles]
        
        # 1. Detect Swing Points (window 5)
        swing_highs = []
        swing_lows = []
        for i in range(5, len(candles) - 5):
            if highs[i] == max(highs[i-5 : i+6]):
                swing_highs.append({"price": highs[i], "index": i})
            if lows[i] == min(lows[i-5 : i+6]):
                swing_lows.append({"price": lows[i], "index": i})
                
        # 2. Equal Highs / Lows (EQH / EQL)
        # Check if last 2-3 swings are within 0.05% of each other
        eqh = False
        eql = False
        eqh_price = 0.0
        eql_price = 0.0
        
        if len(swing_highs) >= 2:
            diff = abs(swing_highs[-1]["price"] - swing_highs[-2]["price"])
            if diff / closes[-1] < 0.0005:  # within 50 pips/points equivalent
                eqh = True
                eqh_price = max(swing_highs[-1]["price"], swing_highs[-2]["price"])
                
        if len(swing_lows) >= 2:
            diff = abs(swing_lows[-1]["price"] - swing_lows[-2]["price"])
            if diff / closes[-1] < 0.0005:
                eql = True
                eql_price = min(swing_lows[-1]["price"], swing_lows[-2]["price"])
                
        # 3. Stop Hunts & Liquidity Grabs
        # Price spikes past EQH/EQL but pulls back below/above
        stop_hunt_bullish = False
        stop_hunt_bearish = False
        
        last_candle = candles[-1]
        
        if eql and last_candle["low"] < eql_price and last_candle["close"] > eql_price:
            stop_hunt_bullish = True
        if eqh and last_candle["high"] > eqh_price and last_candle["close"] < eqh_price:
            stop_hunt_bearish = True
            
        # 4. Liquidity Void
        # Large absolute candles with low/medium volume (price inefficiently delivered)
        voids = []
        for i in range(1, len(candles)):
            body_size = abs(candles[i]["close"] - candles[i]["open"])
            avg_body = np.mean([abs(c["close"] - c["open"]) for c in candles[max(0, i-10) : i]])
            if body_size > 2.5 * avg_body:
                voids.append({"index": i, "top": max(candles[i]["open"], candles[i]["close"]), "bottom": min(candles[i]["open"], candles[i]["close"])})
                
        # Determine resting liquidity
        bsl = eqh_price if eqh else (max(s["price"] for s in swing_highs[-3:]) if swing_highs else max(highs[-20:]))
        ssl = eql_price if eql else (min(s["price"] for s in swing_lows[-3:]) if swing_lows else min(lows[-20:]))
        
        state = "RESTING"
        confidence = 0.60
        reason = "Awaiting liquidity sweep validation."
        
        if stop_hunt_bullish:
            state = "BULL_GRAB"
            confidence = 0.88
            reason = f"Bullish liquidity grab completed below equal lows at {eql_price:.2f}."
        elif stop_hunt_bearish:
            state = "BEAR_GRAB"
            confidence = 0.88
            reason = f"Bearish liquidity grab completed above equal highs at {eqh_price:.2f}."
        elif eqh or eql:
            state = "POOLS_FORMING"
            confidence = 0.75
            reason = f"Resting liquidity pools forming (EQH: {eqh}, EQL: {eql}). Targets defined."
        elif len(voids) > 0 and abs(closes[-1] - voids[-1]["bottom"]) < (closes[-1] * 0.001):
            state = "VOID_ATTRACTION"
            confidence = 0.70
            reason = "Price trading near liquidity void. Expect delivery rebalancing."
            
        metrics = {
            "eqh": eqh,
            "eql": eql,
            "eqh_price": eqh_price,
            "eql_price": eql_price,
            "stop_hunt_bullish": stop_hunt_bullish,
            "stop_hunt_bearish": stop_hunt_bearish,
            "buy_side_liquidity": bsl,
            "sell_side_liquidity": ssl,
            "voids_detected": len(voids)
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
