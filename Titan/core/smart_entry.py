import logging
from typing import Dict, Any, List

logger = logging.getLogger("Titan.SmartEntry")

class SmartEntryEngine:
    
    @staticmethod
    def calculate_entry_parameters(
        action: str, 
        base_price: float, 
        sl_points: int, 
        point_size: float, 
        m1_atr: float,
        decision_score: int,
        regime_desc: str
    ) -> Dict[str, Any]:
        """
        Calculates smart entry levels, including multi-target profit tiers, 
        composite risk-reward ratios, expected hold duration, and signal probabilities.
        """
        # Ensure we don't divide by zero
        if sl_points <= 0:
            sl_points = 150
            
        sl_dist = sl_points * point_size
        
        # 1. Multi-Target Take Profits (TP1 at 1.0 RR, TP2 at 1.5 RR, TP3 at 2.0 RR)
        if action == "BUY":
            stop_loss = base_price - sl_dist
            tp1 = base_price + (1.0 * sl_dist)
            tp2 = base_price + (1.5 * sl_dist)
            tp3 = base_price + (2.0 * sl_dist)
        else: # SELL
            stop_loss = base_price + sl_dist
            tp1 = base_price - (1.0 * sl_dist)
            tp2 = base_price - (1.5 * sl_dist)
            tp3 = base_price - (2.0 * sl_dist)
            
        # 2. Composite expected risk-reward
        # Direct math: 33% trade liquidated at TP1, 33% at TP2, 33% at TP3.
        # Average exit point = (1.0 + 1.5 + 2.0) / 3 = 1.5 R
        composite_rr = 1.5
        
        # 3. Expected Holding Time (in minutes)
        # Based on how long it takes for a candle at the current ATR pace to travel the SL distance
        # e.g., if ATR M1 is 0.40 points, and SL distance is 2.00 points, it takes roughly 5 M1 candles to hit stop.
        atr_pace = m1_atr if m1_atr > 0 else (150 * point_size)
        expected_hold_candles = max(5, int(round((sl_points * point_size) / atr_pace)))
        
        # Round caps (between 10 mins and 60 mins for execution timeframe M1)
        expected_hold_mins = min(60, max(10, expected_hold_candles))
        
        # 4. Expected win probability
        # Calculated from decision confluences score and regime alignment
        # Base probability = 50%
        # Strong Trend and Breakout regimes increase probability by 10%
        p_score = 0.50 + (0.25 * (decision_score / 100.0))
        if regime_desc in ["Strong Trend", "Breakout"]:
            p_score += 0.05
        elif regime_desc in ["Range", "Manipulation"]:
            p_score -= 0.05
            
        expected_prob = min(0.85, max(0.40, p_score))
        
        return {
            "entry_price": base_price,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "expected_rr": composite_rr,
            "expected_hold_time_mins": expected_hold_mins,
            "expected_probability": round(expected_prob, 2)
        }
