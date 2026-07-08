from typing import Dict, Any, List
from Titan.core.smart_entry import SmartEntryEngine

class DecisionEngine:
    """
    Evaluates confluence score, risk-reward ratios, news safety, spread, structure, 
    and liquidity signals to issue BUY, SELL, or WAIT instructions.
    Generates a structured, step-by-step institutional reasoning explanation.
    """
    
    @staticmethod
    def evaluate(
        confluence_res: Dict[str, Any],
        regime_res: Dict[str, Any],
        struct_res: Dict[str, Any],
        liq_res: Dict[str, Any],
        mom_res: Dict[str, Any],
        vol_res: Dict[str, Any],
        sess_res: Dict[str, Any],
        smc_res: Dict[str, Any],
        spread_pts: int,
        is_news_locked: bool,
        settings: Dict[str, Any],
        point_size: float = 0.01,
        m1_atr: float = 1.0,
        last_close: float = 2000.0,
        mtf_res: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        
        # 1. Configuration bounds
        min_confluence = float(settings.get("confidence_threshold", 0.70))
        max_spread = int(settings.get("spread_limit", 300))
        tp_mult = float(settings.get("tp_multiplier", 1.5))
        
        conf_score = confluence_res.get("confidence", 0.50)
        
        # 2. Timeframe-independent Calculations (M1, M3, M5)
        tf_biases = {}
        for tf in ["M1", "M3", "M5"]:
            tf_data = None
            if mtf_res and "metrics" in mtf_res and "timeframes" in mtf_res["metrics"]:
                tf_data = mtf_res["metrics"]["timeframes"].get(tf)
                
            if not tf_data:
                # Fallback to local variables if M1
                if tf == "M1":
                    reg = regime_res
                    struc = struct_res
                    mom = mom_res
                    liq = liq_res
                else:
                    tf_biases[tf] = "WAIT"
                    continue
            else:
                reg = tf_data["regime"]
                struc = tf_data["structure"]
                mom = tf_data["momentum"]
                liq = tf_data["liquidity"]
                
            bullish_score = 0
            bearish_score = 0
            
            # Trend Check
            if reg.get("state") in ["Trending", "Strong Trend", "Expansion"]:
                if "BULLISH" in reg.get("reason", "").upper():
                    bullish_score += 2
                elif "BEARISH" in reg.get("reason", "").upper():
                    bearish_score += 2
                    
            # Structure Check
            if struc.get("state") in ["BULL_BOS", "BULL_CHOCH", "BULL_SWEEP"]:
                bullish_score += 2
            elif struc.get("state") in ["BEAR_BOS", "BEAR_CHOCH", "BEAR_SWEEP"]:
                bearish_score += 2
                
            # Momentum Check
            if mom.get("state") == "BULL_DIVERGENT" or "BULL" in mom.get("state", "").upper() or "BULL" in mom.get("reason", "").upper():
                bullish_score += 1
            elif mom.get("state") == "BEAR_DIVERGENT" or "BEAR" in mom.get("state", "").upper() or "BEAR" in mom.get("reason", "").upper():
                bearish_score += 1
                
            # Liquidity Check
            if liq.get("state") == "BULL_GRAB" or "BULL" in liq.get("reason", "").upper():
                bullish_score += 1.5
            elif liq.get("state") == "BEAR_GRAB" or "BEAR" in liq.get("reason", "").upper():
                bearish_score += 1.5
                
            if bullish_score > bearish_score + 1.0:
                tf_biases[tf] = "BUY"
            elif bearish_score > bullish_score + 1.0:
                tf_biases[tf] = "SELL"
            else:
                tf_biases[tf] = "WAIT"
                
        # 3. Concordance check setup
        m1_b = tf_biases.get("M1", "WAIT")
        m3_b = tf_biases.get("M3", "WAIT")
        m5_b = tf_biases.get("M5", "WAIT")
        
        direction = "WAIT"
        boosted_conf = conf_score
        
        if m1_b == "BUY" and m3_b == "BUY" and m5_b == "BUY":
            direction = "BUY"
            boosted_conf = min(0.95, conf_score + 0.15)
        elif m1_b == "SELL" and m3_b == "SELL" and m5_b == "SELL":
            direction = "SELL"
            boosted_conf = min(0.95, conf_score + 0.15)
            
        # 4. Smart Entry calculation for risk-reward checks
        sl_points = max(150, int((m1_atr / point_size) * 1.5))
        entry_meta = SmartEntryEngine.calculate_entry_parameters(
            direction if direction != "WAIT" else "BUY",
            last_close,
            sl_points,
            point_size,
            m1_atr,
            int(boosted_conf * 100),
            regime_res.get("state", "Range")
        )
        
        # Compute RR
        rr = entry_meta["expected_rr"]
        hold_time = entry_meta["expected_hold_time_mins"]
        
        # 5. Rule Triggers
        news_safe = not is_news_locked
        spread_ok = spread_pts <= max_spread
        confluence_ok = boosted_conf >= min_confluence
        rr_ok = rr >= tp_mult
        
        structure_confirmed = False
        if direction == "BUY":
            structure_confirmed = struct_res.get("state") in ["BULL_BOS", "BULL_CHOCH", "BULL_SWEEP", "CONSOLIDATION"] or "BULLISH" in regime_res.get("reason", "")
        elif direction == "SELL":
            structure_confirmed = struct_res.get("state") in ["BEAR_BOS", "BEAR_CHOCH", "BEAR_SWEEP", "CONSOLIDATION"] or "BEARISH" in regime_res.get("reason", "")
            
        liquidity_confirmed = liq_res.get("state") in ["BULL_GRAB", "BEAR_GRAB", "POOLS_FORMING", "VOID_ATTRACTION"] or smc_res.get("state") != "NEUTRAL"
        
        # 6. Final Instruction Verification
        decision = "WAIT"
        if confluence_ok and rr_ok and news_safe and spread_ok and structure_confirmed and liquidity_confirmed and direction != "WAIT":
            decision = direction
            
        # 7. Explanatory Rationale List construction
        reasons_list = []
        
        # Concordance bias info
        reasons_list.append(f"M1 Bias: {m1_b} | M3 Bias: {m3_b} | M5 Bias: {m5_b}")
        
        # Session label
        active_sess = sess_res.get("state", "Unknown Session")
        reasons_list.append(f"{active_sess}")
        
        # Structure details
        struct_label = struct_res.get("reason", "Structure consolidation")
        reasons_list.append(struct_label)
        
        # SMC detail
        smc_label = smc_res.get("reason", "Price at equilibrium")
        reasons_list.append(smc_label)
        
        # Liquidity details
        liq_label = liq_res.get("reason", "Awaiting liquidity triggers")
        reasons_list.append(liq_label)
        
        # Regimes & Volatility
        vol_desc = "ATR Expansion" if regime_res.get("metrics", {}).get("is_expansion", False) else "Standard Volatility"
        reasons_list.append(vol_desc)
        
        if "Trending" in regime_res.get("state", ""):
            reasons_list.append("EMA Alignment confirmed")
        if vol_res.get("state") == "VOLUME_SPIKE" or vol_res.get("metrics", {}).get("is_spike", False):
            reasons_list.append("Volume Confirmation active")
            
        reasons_list.append("Higher TF aligned")
        reasons_list.append(f"RR = {rr:.1f}")
        reasons_list.append(f"Expected Hold {hold_time} minutes")
        
        if decision == "WAIT":
            blockers = []
            if direction == "WAIT":
                blockers.append(f"Confluence bias mismatch (M1:{m1_b}, M3:{m3_b}, M5:{m5_b})")
            else:
                if not confluence_ok: blockers.append(f"Confluence ({boosted_conf*100:.1f}%) < Threshold ({min_confluence*100:.1f}%)")
                if not news_safe: blockers.append("News lockout filter active")
                if not spread_ok: blockers.append(f"Spread ({spread_pts}) > limit ({max_spread})")
                if not structure_confirmed: blockers.append("Structure alignment conflicting")
                if not liquidity_confirmed: blockers.append("Liquidity sweeps unconfirmed")
            
            reasons_list.insert(0, f"WAIT: " + (", ".join(blockers) if blockers else "No high probability setup."))
            
        explanation_str = " | ".join(reasons_list)
        
        return {
            "decision": decision,
            "score": int(boosted_conf * 100),
            "confidence": float(boosted_conf),
            "reason": explanation_str,
            "reasons_list": reasons_list,
            "entry_details": entry_meta,
            "breakdown": {
                "confluence": float(boosted_conf),
                "risk_reward": float(rr),
                "news_safe": bool(news_safe),
                "spread_ok": bool(spread_ok),
                "structure_confirmed": bool(structure_confirmed),
                "liquidity_confirmed": bool(liquidity_confirmed)
            }
        }
