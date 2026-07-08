from typing import List, Dict, Any

class CandleFlowEngine:
    """
    Computes candlestick flow details for institutional order tracking (Rule 4).
    Calculates body percentages, wick sizes, consecutive strength, and candle patterns like pin bars.
    """
    
    @staticmethod
    def analyze(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(candles) < 5:
            return {
                "body_size": 0.0, "body_pct": 50.0, "upper_wick": 0.0, "lower_wick": 0.0,
                "wick_ratio": 0.0, "close_strength": 0.5, "consecutive_bullish": 0, "consecutive_bearish": 0,
                "pattern": "NEUTRAL"
            }
            
        c = candles[-1]
        prev = candles[-2]
        
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        po, ph, pl, pcl = prev["open"], prev["high"], prev["low"], prev["close"]
        
        body_size = abs(cl - o)
        full_range = max(0.001, h - l)
        body_pct = (body_size / full_range) * 100.0
        
        upper_wick = h - max(o, cl)
        lower_wick = min(o, cl) - l
        wick_ratio = (upper_wick + lower_wick) / full_range
        
        close_strength = (cl - l) / full_range
        open_strength = (o - l) / full_range
        
        # Calculate consecutive streaks
        consecutive_bullish = 0
        consecutive_bearish = 0
        
        for cand in reversed(candles[:-1]):
            if cand["close"] > cand["open"]:
                consecutive_bullish += 1
                consecutive_bearish = 0
            elif cand["close"] < cand["open"]:
                consecutive_bearish += 1
                consecutive_bullish = 0
            else:
                break
                
        # Pattern checks
        pattern = "NEUTRAL"
        is_pin_bar = False
        is_engulfing = False
        is_inside = False
        is_outside = False
        
        # Pin bar: one long wick (> 2x body) and short opposite wick
        if (upper_wick > 2.0 * body_size and lower_wick <= body_size * 1.2):
            is_pin_bar = True
            pattern = "BEARISH_PIN_BAR"
        elif (lower_wick > 2.0 * body_size and upper_wick <= body_size * 1.2):
            is_pin_bar = True
            pattern = "BULLISH_PIN_BAR"
            
        # Engulfing
        prev_body = abs(pcl - po)
        if cl > o and pcl < po and cl > po and o < pcl:
            is_engulfing = True
            pattern = "BULLISH_ENGULFING"
        elif cl < o and pcl > po and cl < po and o > pcl:
            is_engulfing = True
            pattern = "BEARISH_ENGULFING"
            
        # Inside / Outside
        if h < ph and l > pl:
            is_inside = True
            pattern = "INSIDE_BAR"
        elif h > ph and l < pl:
            is_outside = True
            pattern = "OUTSIDE_BAR"
            
        # Rejection & momentum exhaustion logic
        rejection_strength = max(upper_wick, lower_wick) / full_range
        momentum_continuation = True if (cl > o and pcl > po) or (cl < o and pcl < po) else False
        
        return {
            "body_size": float(body_size),
            "body_pct": float(body_pct),
            "upper_wick": float(upper_wick),
            "lower_wick": float(lower_wick),
            "wick_ratio": float(wick_ratio),
            "close_strength": float(close_strength),
            "open_strength": float(open_strength),
            "consecutive_bullish": consecutive_bullish,
            "consecutive_bearish": consecutive_bearish,
            "rejection_strength": float(rejection_strength),
            "momentum_continuation": bool(momentum_continuation),
            "pattern": pattern,
            "is_pin_bar": is_pin_bar,
            "is_engulfing": is_engulfing,
            "is_inside": is_inside,
            "is_outside": is_outside
        }
