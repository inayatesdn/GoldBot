from typing import List, Dict, Any
from Titan.market.intelligence.regime import MarketRegimeEngine
from Titan.market.intelligence.structure import StructureEngine
from Titan.market.intelligence.liquidity import LiquidityEngine
from Titan.market.intelligence.smc import SmartMoneyEngine
from Titan.market.intelligence.momentum import MomentumEngine
from Titan.market.intelligence.volume import VolumeEngine
from Titan.market.intelligence.session import SessionEngine

class MultiTimeframeEngine:
    """
    Evaluates trend, structure, momentum, and liquidity across:
    M1, M3, M5, M15, M30, H1.
    Calculates overall multi-timeframe alignment scores.
    Returns confidence, reason, state, and metrics.
    """
    
    @staticmethod
    def analyze(all_timeframes_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        required_tfs = ["M1", "M3", "M5", "M15", "M30", "H1"]
        
        # Check if all timeframes are present
        results = {}
        for tf in required_tfs:
            candles = all_timeframes_data.get(tf, [])
            if len(candles) < 20: 
                # Fill with mock/empty data if timeframe lacks candles
                results[tf] = {
                    "regime": {"state": "Range", "confidence": 0.5},
                    "structure": {"state": "CONSOLIDATION", "confidence": 0.5},
                    "liquidity": {"state": "RESTING", "confidence": 0.5},
                    "smc": {"state": "NEUTRAL", "confidence": 0.5},
                    "momentum": {"state": "NEUTRAL", "confidence": 0.5},
                    "volume": {"state": "NORMAL", "confidence": 0.5},
                    "session": {"state": "London", "confidence": 0.5}
                }
            else:
                results[tf] = {
                    "regime": MarketRegimeEngine.classify(candles),
                    "structure": StructureEngine.analyze(candles),
                    "liquidity": LiquidityEngine.analyze(candles),
                    "smc": SmartMoneyEngine.analyze(candles),
                    "momentum": MomentumEngine.analyze(candles),
                    "volume": VolumeEngine.analyze(candles),
                    "session": SessionEngine.analyze(candles)
                }
                
        # 2. Evaluate Higher Timeframe (HTF) Alignment
        # Macro trend alignment: H1 trend + M30 trend + M15 trend
        h1_trend = results["H1"]["regime"]["state"]
        m30_trend = results["M30"]["regime"]["state"]
        m15_trend = results["M15"]["regime"]["state"]
        m5_trend = results["M5"]["regime"]["state"]
        
        # Count bullish/bearish indicators on HTFs
        bullish_votes = 0
        bearish_votes = 0
        
        for tf in ["M5", "M15", "M30", "H1"]:
            state = results[tf]["regime"]["state"]
            if state in ["Trending", "Strong Trend", "Expansion"] or "BULLISH" in results[tf]["regime"]["reason"]:
                bullish_votes += 1
            elif state in ["Reversal"] and "BEARISH" in results[tf]["regime"]["reason"]:
                bearish_votes += 1
            elif state in ["Trending", "Strong Trend"] and "BEARISH" in results[tf]["regime"]["reason"]:
                bearish_votes += 1
                
        # Confluence state
        state = "NEUTRAL"
        confidence = 0.50
        reason = "Higher timeframes are conflicting or consolidating."
        
        if bullish_votes >= 3:
            state = "BULLISH_ALIGNMENT"
            confidence = 0.85
            reason = f"Strong Bullish Alignment across higher timeframes ({bullish_votes}/4 bull signals)."
        elif bearish_votes >= 3:
            state = "BEARISH_ALIGNMENT"
            confidence = 0.85
            reason = f"Strong Bearish Alignment across higher timeframes ({bearish_votes}/4 bear signals)."
            
        metrics = {
            "bullish_votes": bullish_votes,
            "bearish_votes": bearish_votes,
            "h1_state": h1_trend,
            "m30_state": m30_trend,
            "m15_state": m15_trend,
            "m5_state": m5_trend,
            "timeframes": results
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
