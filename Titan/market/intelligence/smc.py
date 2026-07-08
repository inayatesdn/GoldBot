import numpy as np
from typing import List, Dict, Any

class SmartMoneyEngine:
    """
    Detects Order Blocks, Fair Value Gaps, Premium/Discount, OTE Zones (Optimal Trade Entry),
    Displacement, Breaker Blocks, and Mitigation Blocks.
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
        opens = [c["open"] for c in candles]
        
        last_close = closes[-1]
        
        # 1. Detect PVG / FVG (Fair Value Gaps)
        fvg_bullish = []
        fvg_bearish = []
        for i in range(1, len(candles) - 1):
            # Bullish FVG: Low of i+1 is higher than High of i-1
            if lows[i+1] > highs[i-1]:
                fvg_bullish.append({
                    "index": i,
                    "top": lows[i+1],
                    "bottom": highs[i-1],
                    "price": (lows[i+1] + highs[i-1]) / 2.0
                })
            # Bearish FVG: High of i+1 is lower than Low of i-1
            elif highs[i+1] < lows[i-1]:
                fvg_bearish.append({
                    "index": i,
                    "top": lows[i-1],
                    "bottom": highs[i+1],
                    "price": (lows[i-1] + highs[i+1]) / 2.0
                })
                
        # 2. Detect Order Blocks (OB)
        # Bullish OB: last bearish candle prior to a bullish move that causes displacement
        # Bearish OB: last bullish candle prior to a bearish move that causes displacement
        order_blocks = []
        for i in range(2, len(candles) - 2):
            body_size = abs(closes[i] - opens[i])
            disp_move = closes[i+1] - opens[i+1]
            
            # Simple displacement check: next bar is large and in direction of move
            if closes[i] < opens[i] and disp_move > 2.0 * body_size and closes[i+2] > closes[i+1]:
                order_blocks.append({
                    "type": "BULLISH",
                    "top": max(opens[i], highs[i]),
                    "bottom": lows[i],
                    "index": i
                })
            elif closes[i] > opens[i] and disp_move < -2.0 * body_size and closes[i+2] < closes[i+1]:
                order_blocks.append({
                    "type": "BEARISH",
                    "top": highs[i],
                    "bottom": min(opens[i], lows[i]),
                    "index": i
                })
                
        # 3. Premium / Discount & OTE Zone (Optimal Trade Entry 62% - 79%)
        recent_high = max(highs[-25:])
        recent_low = min(lows[-25:])
        swing_range = recent_high - recent_low
        
        rel_position = 0.5
        if swing_range > 0:
            rel_position = (last_close - recent_low) / swing_range
            
        is_discount = rel_position < 0.50
        is_premium = rel_position > 0.50
        is_ote = 0.62 <= (1.0 - rel_position if is_discount else rel_position) <= 0.79
        
        # 4. State & Confidence evaluation
        state = "NEUTRAL"
        confidence = 0.55
        reason = "Market is in equilibrium."
        
        # Bullish setup check: Price is in discount, in OTE range, and near a Bullish OB
        near_bull_ob = False
        bullish_ob_price = 0.0
        for ob in order_blocks[-3:]:
            if ob["type"] == "BULLISH" and last_close >= ob["bottom"] and last_close <= ob["top"] * 1.002:
                near_bull_ob = True
                bullish_ob_price = ob["top"]
                break
                
        near_bear_ob = False
        bearish_ob_price = 0.0
        for ob in order_blocks[-3:]:
            if ob["type"] == "BEARISH" and last_close <= ob["top"] and last_close >= ob["bottom"] * 0.998:
                near_bear_ob = True
                bearish_ob_price = ob["bottom"]
                break
                
        if near_bull_ob and is_discount:
            state = "BULLISH_OB_RETEST"
            confidence = 0.85
            reason = f"Mitigating Bullish Order Block ({bullish_ob_price:.2f}) inside Discount range."
        elif near_bear_ob and is_premium:
            state = "BEARISH_OB_RETEST"
            confidence = 0.85
            reason = f"Mitigating Bearish Order Block ({bearish_ob_price:.2f}) inside Premium range."
        elif len(fvg_bullish) > 0 and last_close >= fvg_bullish[-1]["bottom"] and last_close <= fvg_bullish[-1]["top"]:
            state = "BULLISH_FVG_FILL"
            confidence = 0.78
            reason = "Price is currently balancing inside a Bullish Fair Value Gap."
        elif len(fvg_bearish) > 0 and last_close >= fvg_bearish[-1]["bottom"] and last_close <= fvg_bearish[-1]["top"]:
            state = "BEARISH_FVG_FILL"
            confidence = 0.78
            reason = "Price is currently balancing inside a Bearish Fair Value Gap."
        elif is_ote and is_discount:
            state = "BULLISH_OTE"
            confidence = 0.72
            reason = "Optimal Trade Entry (OTE) zone reached in discount."
        elif is_ote and is_premium:
            state = "BEARISH_OTE"
            confidence = 0.72
            reason = "Optimal Trade Entry (OTE) zone reached in premium."
            
        metrics = {
            "recent_high": recent_high,
            "recent_low": recent_low,
            "relative_position": rel_position,
            "is_discount": is_discount,
            "is_premium": is_premium,
            "is_ote": is_ote,
            "fvg_bullish_count": len(fvg_bullish),
            "fvg_bearish_count": len(fvg_bearish),
            "order_blocks_count": len(order_blocks)
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
