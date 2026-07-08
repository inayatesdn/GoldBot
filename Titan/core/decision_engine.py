import logging
from typing import Dict, Any

logger = logging.getLogger("Titan.DecisionEngine")

class DecisionEngine:
    
    @staticmethod
    def evaluate_setup(
        confluence_payload: Dict[str, Any], 
        spread_pts: int, 
        news_lock_active: bool,
        session_active: bool,
        risk_reward_est: float,
        settings: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Calculates the institutional confluence score out of 100 points.
        Examines multi-timeframe concordance, structure sweeps, volatility, momentum,
        spread quality, news context, and estimated risk/reward.
        
        Returns:
            Dict: {decision: str, score: int, confidence: float, reason: str, breakdown: Dict}
        """
        score = 0
        breakdown = {
            "trend_alignment": 0,
            "market_structure": 0,
            "fvg_ob_reactions": 0,
            "volatility_regime": 0,
            "momentum_metrics": 0,
            "session_timing": 0,
            "spread_quality": 0,
            "news_filter": 0,
            "risk_reward": 0
        }
        
        # 1. Trend Alignment (Max 30 points)
        if confluence_payload.get("trend_aligned", False):
            score += 30
            breakdown["trend_alignment"] = 30
        else:
            # Partial trend points if H1 is aligned with M3 at least
            if confluence_payload.get("macro_trend") == confluence_payload.get("conf_trend"):
                score += 15
                breakdown["trend_alignment"] = 15
                
        # 2. Market Structure (BOS / CHoCH) (Max 20 points)
        # Evaluated on M1 and M3
        struct_pts = 0
        m1_struct = confluence_payload.get("m1_metrics", {})
        m3_struct = confluence_payload.get("m3_metrics", {})
        
        if m1_struct.get("bos") or m3_struct.get("bos"):
            struct_pts += 10
        if m1_struct.get("choch") or m3_struct.get("choch"):
            struct_pts += 10
            
        score += struct_pts
        breakdown["market_structure"] = struct_pts
        
        # 3. FVG and Order Block Reaction (Max 15 points)
        fvg_ob_pts = 0
        if m1_struct.get("ob_touched") or m3_struct.get("ob_touched"):
            fvg_ob_pts += 10
        if m1_struct.get("fvg_touched") or m3_struct.get("fvg_touched"):
            fvg_ob_pts += 5
            
        score += fvg_ob_pts
        breakdown["fvg_ob_reactions"] = fvg_ob_pts
        
        # 4. Volatility Regime (Max 10 points)
        vol_pts = 0
        m5_metrics = confluence_payload.get("m5_metrics", {})
        # If ADX on trend TF M5 is > 25, trend is strong
        m5_indicators = m5_metrics.get("indicators", {}) if isinstance(m5_metrics.get("indicators"), dict) else {}
        adx_val = m5_metrics.get("adx", 25.0)
        # Check standard indicators mapping
        if adx_val > 25.0:
            vol_pts += 10
        else:
            vol_pts += 5
        score += vol_pts
        breakdown["volatility_regime"] = vol_pts
        
        # 5. Momentum confirmation (Max 10 points)
        mom_pts = 0
        target_dir = confluence_payload.get("macro_trend", "BULLISH")
        m1_rsi = m1_struct.get("rsi", 50.0)
        
        if target_dir == "BULLISH":
            if m1_rsi < 45:
                mom_pts += 5
            if m1_struct.get("macd_bullish"):
                mom_pts += 5
        else: # BEARISH
            if m1_rsi > 55:
                mom_pts += 5
            if m1_struct.get("macd_bearish"):
                mom_pts += 5
                
        score += mom_pts
        breakdown["momentum_metrics"] = mom_pts
        
        # 6. Session timing (Max 5 points)
        sess_pts = 5 if session_active else 0
        score += sess_pts
        breakdown["session_timing"] = sess_pts
        
        # 7. Spread Quality (Max 5 points)
        spd_lim = settings.get("spread_limit", 300)
        spread_pts_val = 5 if (spread_pts <= spd_lim) else 0
        score += spread_pts_val
        breakdown["spread_quality"] = spread_pts_val
        
        # 8. News filter check (Max 5 points)
        news_pts = 0 if news_lock_active else 5
        score += news_pts
        breakdown["news_filter"] = news_pts
        
        # Select target action
        action_decision = "WAIT"
        conf_threshold = int(settings.get("confidence_threshold", 0.70) * 100)
        
        if score >= conf_threshold and confluence_payload.get("trend_aligned", False):
            action_decision = "BUY" if target_dir == "BULLISH" else "SELL"
            
        reasons = []
        if breakdown["trend_alignment"] >= 30:
            reasons.append("Perfect multi-timeframe trend alignment")
        if breakdown["market_structure"] > 0:
            reasons.append("Structural sweeps (BOS/CHoCH) detected")
        if breakdown["fvg_ob_reactions"] > 0:
            reasons.append("Key FVG page imbalances/Order blocks hit")
        if news_lock_active:
            reasons.append("LOCK: news lock filters active")
        if spread_pts > spd_lim:
            reasons.append(f"FILTER: Spread ({spread_pts}) wider than allowed limit ({spd_lim})")
            
        reason_str = "; ".join(reasons) if reasons else "Awaiting consensus alignment setup..."
        
        return {
            "decision": action_decision,
            "score": score,
            "confidence": round(score / 100.0, 2),
            "reason": reason_str,
            "breakdown": breakdown
        }
