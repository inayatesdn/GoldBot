from typing import Dict, Any

class ConfluenceEngine:
    """
    Combines individual engine outcomes into a weighted confluence score out of 100.
    Trend: 18, Structure: 20, Liquidity: 16, Momentum: 12, Volume: 10, News: 10, Session: 6, Volatility: 8.
    Returns confidence, reason, state, and metrics.
    """
    
    @staticmethod
    def calculate(
        regime_res: Dict[str, Any],
        struct_res: Dict[str, Any],
        liq_res: Dict[str, Any],
        mom_res: Dict[str, Any],
        vol_res: Dict[str, Any],
        sess_res: Dict[str, Any],
        is_news_locked: bool
    ) -> Dict[str, Any]:
        
        # 1. Weights
        w_trend = 18
        w_struct = 20
        w_liq = 16
        w_mom = 12
        w_vol = 10
        w_news = 10
        w_session = 6
        w_volatility = 8
        
        # 2. Derive Scores from Confidence
        s_trend = regime_res.get("confidence", 0.5) * w_trend
        s_struct = struct_res.get("confidence", 0.5) * w_struct
        s_liq = liq_res.get("confidence", 0.5) * w_liq
        s_mom = mom_res.get("confidence", 0.5) * w_mom
        s_vol = vol_res.get("confidence", 0.5) * w_vol
        s_news = 0.0 if is_news_locked else float(w_news)
        s_session = sess_res.get("confidence", 0.5) * w_session
        
        # Volatility check
        vol_state = regime_res.get("metrics", {}).get("volatility_ratio", 1.0)
        if vol_state >= 1.5: # HIGH
            s_volatility = float(w_volatility)
        elif vol_state < 0.70: # LOW
            s_volatility = float(w_volatility * 0.4)
        else: # NORMAL
            s_volatility = float(w_volatility * 0.8)
            
        total_score = s_trend + s_struct + s_liq + s_mom + s_vol + s_news + s_session + s_volatility
        total_score = min(100.0, max(0.0, total_score))
        
        # State
        state = "CONFLUENTIAL" if total_score >= 70.0 else "WEAK_CONFLUENCE"
        confidence = total_score / 100.0
        
        reason = f"Combined Confluence: {total_score:.1f}% (Trend: {s_trend:.1f}, Structure: {s_struct:.1f}, Liquidity: {s_liq:.1f}, Momentum: {s_mom:.1f}, Volume: {s_vol:.1f})"
        
        metrics = {
            "total_score": total_score,
            "trend_score": s_trend,
            "structure_score": s_struct,
            "liquidity_score": s_liq,
            "momentum_score": s_mom,
            "volume_score": s_vol,
            "news_score": s_news,
            "session_score": s_session,
            "volatility_score": s_volatility
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": state,
            "metrics": metrics
        }
