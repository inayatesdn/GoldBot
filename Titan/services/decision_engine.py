import time
import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
import MetaTrader5 as mt5

from Titan.config.config import PRIMARY_SYMBOL, get_settings
from Titan.core.state import state
from Titan.core.logger import system_logger
from Titan.market.sessions import SessionManager
from Titan.market.economic_calendar import EconomicCalendar
from Titan.core.smart_entry import SmartEntryEngine
from Titan.market.intelligence.regime import MarketRegimeEngine
from Titan.market.intelligence.structure import StructureEngine
from Titan.market.intelligence.smc import SmartMoneyEngine
from Titan.market.intelligence.candle_flow import CandleFlowEngine

class DecisionEngine:
    def __init__(self, symbol: str = PRIMARY_SYMBOL):
        self.symbol = symbol
        self.logger = system_logger

    def evaluate_signals(self) -> Dict[str, Any]:
        """Calculates trade confluences dynamically based on in-memory candles and MT5 higher timeframe data."""
        state.lock.acquire()
        m1_candles = list(state.candles)
        bid = state.bid
        ask = state.ask
        spread = state.spread
        is_connected = state.mt5_connected
        state.lock.release()

        if not is_connected or len(m1_candles) < 30:
            return {
                "decision": "WAIT",
                "score": 0,
                "confidence": 0.0,
                "reason": "WAIT: Insufficient candle history or MT5 disconnected.",
                "regime": "N/A", "trend": "N/A", "momentum": "N/A", "volatility": "N/A", "structure": "N/A", "liquidity": "N/A",
                "entry": 0.0, "sl": 0.0, "tp": 0.0, "expected_rr": "N/A", "expected_hold": "N/A",
                "next_setup": "Awaiting market data stream...", "time_until_next": "0s"
            }

        settings = get_settings()
        curr_price = (bid + ask) / 2.0
        
        # 1. Calculate Indicators on execution candles (M1)
        closes = [c["close"] for c in m1_candles]
        highs = [c["high"] for c in m1_candles]
        lows = [c["low"] for c in m1_candles]
        vols = [c["tick_volume"] for c in m1_candles]

        # Calculate EMA
        m1_ema20 = self.calculate_ema(closes, 20)[-1]
        m1_ema50 = self.calculate_ema(closes, 50)[-1]
        
        # Calculate ATR
        m1_atr14 = self.calculate_atr(m1_candles, 14)[-1]
        
        # Calculate RSI
        m1_rsi14 = self.calculate_rsi(closes, 14)[-1]
        
        # Calculate VWAP
        m1_vwap = self.calculate_vwap(m1_candles)[-1]
        
        # Calculate Momentum (ROC 14)
        m1_mom14 = self.calculate_momentum(closes, 14)[-1]
        
        # Compute Volatility
        volatility_ratio = m1_atr14 / (curr_price * 0.0005) if curr_price > 0 else 1.0
        volatility_status = "NORMAL"
        if volatility_ratio > 1.8:
            volatility_status = "EXPANDING (HIGH)"
        elif volatility_ratio < 0.6:
            volatility_status = "COMPRESSED (LOW)"

        # Calculate Volume Profile (Relative volume RVOL indicator)
        avg_vol_20 = sum(vols[-20:]) / 20.0 if len(vols) >= 20 else 100.0
        current_rvol = vols[-1] / avg_vol_20 if avg_vol_20 > 0 else 1.0
        
        # 2. Extract HTF, M5, and M15 Trends (Step 1)
        htf_trend = "NEUTRAL"
        h1_rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_H1, 0, 30)
        if h1_rates is not None and len(h1_rates) >= 20:
            h1_closes = [c[4] for c in h1_rates]
            h1_ema10 = self.calculate_ema(h1_closes, 10)[-1]
            h1_ema20 = self.calculate_ema(h1_closes, 20)[-1]
            if h1_ema10 > h1_ema20:
                htf_trend = "BULLISH"
            elif h1_ema10 < h1_ema20:
                htf_trend = "BEARISH"
                
        m5_trend = "NEUTRAL"
        m5_rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M5, 0, 30)
        if m5_rates is not None and len(m5_rates) >= 20:
            m5_closes = [c[4] for c in m5_rates]
            m5_ema10 = self.calculate_ema(m5_closes, 10)[-1]
            m5_ema20 = self.calculate_ema(m5_closes, 20)[-1]
            if m5_ema10 > m5_ema20:
                m5_trend = "BULLISH"
            elif m5_ema10 < m5_ema20:
                m5_trend = "BEARISH"

        m15_trend = "NEUTRAL"
        m15_rates = mt5.copy_rates_from_pos(self.symbol, mt5.TIMEFRAME_M15, 0, 30)
        if m15_rates is not None and len(m15_rates) >= 20:
            m15_closes = [c[4] for c in m15_rates]
            m15_ema10 = self.calculate_ema(m15_closes, 10)[-1]
            m15_ema20 = self.calculate_ema(m15_closes, 20)[-1]
            if m15_ema10 > m15_ema20:
                m15_trend = "BULLISH"
            elif m15_ema10 < m15_ema20:
                m15_trend = "BEARISH"
                
        # Run Modular Intelligence Engines (Rules 4, 5, 6, 7, 8, 9, 10)
        regime_res = MarketRegimeEngine.classify(m1_candles)
        struct_res = StructureEngine.analyze(m1_candles)
        smc_res = SmartMoneyEngine.analyze(m1_candles)
        candle_flow_res = CandleFlowEngine.analyze(m1_candles)

        structure_state = struct_res["state"]
        liq_state = "SWEPT" if (struct_res["metrics"]["sweep_bullish"] or struct_res["metrics"]["sweep_bearish"]) else "RESTING"

        # 4. Check News Lock and Session Info
        news_lock_active, min_left, news_title = EconomicCalendar.check_news_lock()
        sess_data = SessionManager.get_current_sessions()
        session_active = False
        configured_session = settings.get("trading_session", "London-New York Overlap")
        if configured_session == "All":
            session_active = True
        else:
            for s in sess_data["active_sessions"] + sess_data["overlaps"]:
                if configured_session.lower() in s.lower() or s.lower() in configured_session.lower():
                    session_active = True
                    break

        # 5. Strategy Marketplace (5 voting strategies)
        votes = []
        reasons = []
        
        # Strategy A: Scalper (EMA & VWAP crossover)
        v_scalper = "WAIT"
        m1_trend = "BULLISH" if m1_ema20 > m1_ema50 else "BEARISH"
        if curr_price > m1_vwap and m1_trend == "BULLISH":
            v_scalper = "BUY"
        elif curr_price < m1_vwap and m1_trend == "BEARISH":
            v_scalper = "SELL"
        votes.append(v_scalper)
        reasons.append(f"Scalper Strategy: {v_scalper}")

        # Strategy B: Macro Trend (M1 vs M5 trend agreement)
        v_trend = "WAIT"
        if m1_trend == htf_trend:
            v_trend = m1_trend
        votes.append(v_trend)
        reasons.append(f"Trend Strategy: {v_trend}")

        # Strategy C: Liquidity Sweep
        v_liquidity = "WAIT"
        if struct_res["state"] in ["BULL_SWEEP", "BULL_BOS"]:
            v_liquidity = "BUY"
        elif struct_res["state"] in ["BEAR_SWEEP", "BEAR_BOS"]:
            v_liquidity = "SELL"
        votes.append(v_liquidity)
        reasons.append(f"Liquidity Sweep Strategy: {v_liquidity}")

        # Strategy D: Order Block Demand/Supply Areas
        v_ob = "WAIT"
        if smc_res["state"] == "BULLISH_OB_RETEST":
            v_ob = "BUY"
        elif smc_res["state"] == "BEARISH_OB_RETEST":
            v_ob = "SELL"
        votes.append(v_ob)
        reasons.append(f"Order Block Strategy: {v_ob}")

        # Strategy E: Momentum Pullback
        v_momentum = "WAIT"
        if m1_rsi14 < 45 and m1_mom14 > 0:
            v_momentum = "BUY"
        elif m1_rsi14 > 55 and m1_mom14 < 0:
            v_momentum = "SELL"
        votes.append(v_momentum)
        reasons.append(f"Momentum Strategy: {v_momentum}")

        # 6. Vote Aggregation & Decision Logic
        buy_votes = votes.count("BUY")
        sell_votes = votes.count("SELL")
        total_voters = len(votes)
        
        candidate_decision = "WAIT"
        if buy_votes >= 3:
            candidate_decision = "BUY"
        elif sell_votes >= 3:
            candidate_decision = "SELL"
            
        # Rule 11 & Step 1 - Trade Direction Validation (Independent BUY/SELL Scores out of 100)
        buy_score = 0
        sell_score = 0
        
        # 1. Higher timeframe trend (max 30 pts)
        if htf_trend == "BULLISH": buy_score += 10
        elif htf_trend == "BEARISH": sell_score += 10
        
        if m15_trend == "BULLISH": buy_score += 10
        elif m15_trend == "BEARISH": sell_score += 10

        if m5_trend == "BULLISH": buy_score += 10
        elif m5_trend == "BEARISH": sell_score += 10
            
        # 2. Market Structure (20 pts)
        if struct_res["state"] in ["BULL_BOS", "BULL_CHOCH"]: buy_score += 20
        elif struct_res["state"] in ["BEAR_BOS", "BEAR_CHOCH"]: sell_score += 20
        elif struct_res["state"] == "BULL_SWEEP": buy_score += 10
        elif struct_res["state"] == "BEAR_SWEEP": sell_score += 10
            
        # 3. Liquidity (15 pts)
        if struct_res["metrics"]["sweep_bullish"]: buy_score += 15
        if struct_res["metrics"]["sweep_bearish"]: sell_score += 15
            
        # 4. Order Block (15 pts)
        if smc_res["state"] == "BULLISH_OB_RETEST": buy_score += 15
        elif smc_res["state"] == "BEARISH_OB_RETEST": sell_score += 15
            
        # 5. Momentum (10 pts)
        if m1_rsi14 < 45 and m1_mom14 > 0: buy_score += 10
        elif m1_rsi14 > 55 and m1_mom14 < 0: sell_score += 10

        # General Environment Conditions (Apply equally if entering, mostly just scales confidence threshold)
        base_env_score = 0
        if current_rvol > 1.0: base_env_score += 5
        if spread <= 200: base_env_score += 5
        buy_score += base_env_score
        sell_score += base_env_score

        # Ensure bounds
        buy_score = min(100, buy_score)
        sell_score = min(100, sell_score)
        
        # 6. Final Decision & Direction Resolution (Step 1)
        score = 0
        candidate_decision = "WAIT"
        if buy_votes >= 3 and buy_score > sell_score:
            candidate_decision = "BUY"
            score = buy_score
        elif sell_votes >= 3 and sell_score > buy_score:
            candidate_decision = "SELL"
            score = sell_score
        else:
            candidate_decision = "WAIT"
            score = max(buy_score, sell_score)

        # Adjust score for session / news lockout
        if not session_active:
            buy_score = max(0, buy_score - 20)
            sell_score = max(0, sell_score - 20)
            score = max(0, score - 20)
        if news_lock_active:
            buy_score = 0
            sell_score = 0
            score = 0
            
        conf_threshold = int(settings.get("confidence_threshold", 70))
        
        decision = "WAIT"
        similar_win_rate = 100.0
        similar_count = 0
        
        if candidate_decision != "WAIT" and score >= conf_threshold:
            from Titan.learning.learning_engine import LearningEngine as CoreLearningEngine
            from Titan.storage.db import get_db_connection
            sh_conn = get_db_connection()
            similar_actionable = True
            try:
                setup_stats = CoreLearningEngine.search_similar_setups(
                    sh_conn, self.symbol, regime_res["state"], sess_data.get("session_desc", "NY Session"), candidate_decision
                )
                similar_count = setup_stats["count"]
                similar_win_rate = setup_stats["win_rate"]
                similar_actionable = setup_stats["actionable"]
                
                if not similar_actionable:
                    self.logger.warning(
                        f"[Rule 4 Setup Block] Candidate {candidate_decision} rejected. "
                        f"Past statistical win rate matches regime/session is poor: {similar_win_rate}% over {similar_count} trades."
                    )
                    reason_str = (
                        f"WAIT: Rejected by Rule 4 - Similar setups performed poorly "
                        f"({similar_win_rate}% Win Rate over {similar_count} setups)."
                    )
                    score = min(score, 50) # force score down
                    decision = "WAIT"
                else:
                    decision = candidate_decision
            except Exception as e:
                self.logger.error(f"Error checking similar setups: {e}")
                decision = candidate_decision
            finally:
                sh_conn.close()

        # 7. Pattern Recognition Query (Historical wins lookup)
        setup_won_count = 0
        setup_total_count = 0
        from Titan.storage.db import get_db_connection
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) as total, SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins FROM trades WHERE status='CLOSED' AND direction = ?",
                (decision if decision != "WAIT" else "BUY",)
            )
            row = cursor.fetchone()
            if row:
                setup_total_count = int(row["total"])
                setup_won_count = int(row["wins"]) if row["wins"] is not None else 0
        except Exception as e:
            self.logger.error(f"Error querying pattern stats: {e}")
        finally:
            conn.close()
            
        # expected probability AI
        base_probability = 0.50
        if decision != "WAIT":
            agreement_ratio = votes.count(decision) / total_voters
            hist_blend = (setup_won_count / setup_total_count) if setup_total_count > 0 else 0.82
            prob_val = base_probability + (agreement_ratio * 0.32) + (0.05 if session_active else 0.0) + (hist_blend * 0.10)
        else:
            prob_val = 0.0
        win_probability = min(0.98, max(0.40, prob_val)) if decision != "WAIT" else 0.0

        # 8. Trade Quality Engine (Grade, Expected hold, Expected Win Rate)
        grade = "C"
        risk_grade = "MODERATE"
        if score >= 90:
            grade = "A+"
            risk_grade = "VERY LOW"
        elif score >= 80:
            grade = "A"
            risk_grade = "LOW"
        elif score >= 70:
            grade = "B"
            risk_grade = "MODERATE"
            
        # 9. Predictive Engine (10s, 30s, 60s prediction)
        roc_accel = m1_mom14 - sum(vols[-5:]) / 50.0  # simple tick speed vs momentum metric
        pred_10s = "BULLISH" if roc_accel > 0.0 else "BEARISH"
        pred_30s = "BULLISH" if m1_mom14 > 0.0 else "BEARISH"
        pred_60s = "BULLISH" if m1_trend == "BULLISH" else "BEARISH"
        
        expected_move_dollars = m1_atr14 * (1.2 if pred_30s == "BULLISH" else -1.2)
        
        # 10. AI Commander Explanation Builder
        active_sess_desc = sess_data.get("session_desc", "NY Session")
        reason_list = []
        if decision != "WAIT":
            reason_list.append(f"✓ {active_sess_desc}")
            reason_list.append(f"✓ Trend: {m1_trend}")
            reason_list.append(f"✓ Structure: {structure_state}")
            reason_list.append(f"✓ Score: {score}/100")
            reason_list.append(f"✓ Pattern: {setup_won_count}/{setup_total_count}")
            reason_str = " | ".join(reason_list)
        else:
            reason_str = f"WAIT: Configured threshold ({conf_threshold}%) not met. "
            if candidate_decision == "WAIT":
                reason_str += f"Conflict - BUY Score: {buy_score}, SELL Score: {sell_score} | "
            else:
                reason_str += f"{candidate_decision} score was only {score}/100 | "
            reason_str += f"Spread: {spread} pts."

        # Calculate entry levels - Rule 12 & Rule 15
        point_size = 0.01
        sym_info = mt5.symbol_info(self.symbol)
        if sym_info:
            point_size = sym_info.point
            
        atr_mult = settings.get("atr_multiplier", 1.5)
        tp_mult = settings.get("tp_multiplier", 1.5)
        
        atr_points = round(m1_atr14 / point_size) if m1_atr14 > 0 else 150
        sl_points = max(150, min(500, int(atr_points * atr_mult)))
        
        entry_meta = SmartEntryEngine.calculate_entry_parameters(
            decision if decision != "WAIT" else "BUY",
            curr_price,
            sl_points,
            point_size,
            m1_atr14,
            score,
            structure_state
        )

        output = {
            "timestamp": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": self.symbol,
            "decision": decision,
            "score": score,
            "buy_score": buy_score,
            "sell_score": sell_score,
            "confidence": win_probability,
            "similar_setups_win_rate": similar_win_rate,
            "similar_setups_count": similar_count,
            "reason": reason_str,
            "regime": regime_res["state"],
            "trend": m1_trend,
            "momentum": f"RSI: {m1_rsi14:.1f} | Momentum: {m1_mom14:.3f}",
            "volatility": f"ATR: {m1_atr14:.2f} ({volatility_status})",
            "structure": structure_state,
            "liquidity": liq_state,
            "entry": curr_price,
            "sl": entry_meta["stop_loss"] if decision != "WAIT" else 0.0,
            "tp": entry_meta["tp1"] if decision != "WAIT" else 0.0,
            "expected_rr": f"{tp_mult:.1f}:1",
            "expected_hold": f"{entry_meta['expected_hold_time_mins']}m",
            "next_setup": "Monitoring structural sweeps...",
            "time_until_next": "Tick streaming active",
            "grade": grade,
            "risk_grade": risk_grade,
            "expected_win_probability": f"{win_probability * 100:.2f}%",
            "expected_movement": f"{expected_move_dollars:+.2f} USD",
            "pred_10s": pred_10s,
            "pred_30s": pred_30s,
            "pred_60s": pred_60s,
            "votes": {
                "v_scalper": v_scalper,
                "v_trend": v_trend,
                "v_liquidity": v_liquidity,
                "v_ob": v_ob,
                "v_momentum": v_momentum
            }
        }
        
        # Save to global state
        state.lock.acquire()
        state.latest_decision = output
        state.lock.release()

        return output

    # Helper math equations
    def calculate_ema(self, prices: List[float], period: int) -> List[float]:
        if len(prices) < period:
            return [prices[-1]] * len(prices)
        multiplier = 2.0 / (period + 1.0)
        ema = [sum(prices[:period]) / period]
        for p in prices[period:]:
            ema.append((p - ema[-1]) * multiplier + ema[-1])
        return [ema[0]] * (period - 1) + ema

    def calculate_atr(self, candles: List[Dict[str, Any]], period: int) -> List[float]:
        if len(candles) < period + 1:
            return [0.1] * len(candles)
        tr = [candles[0]["high"] - candles[0]["low"]]
        for i in range(1, len(candles)):
            h = candles[i]["high"]
            l = candles[i]["low"]
            yc = candles[i-1]["close"]
            tr.append(max(h - l, abs(h - yc), abs(l - yc)))
        atr = [sum(tr[:period]) / period]
        for t in tr[period:]:
            atr.append((atr[-1] * (period - 1) + t) / period)
        return [atr[0]] * (period - 1) + atr

    def calculate_rsi(self, prices: List[float], period: int) -> List[float]:
        if len(prices) < period + 1:
            return [50.0] * len(prices)
        gains = []
        losses = []
        for i in range(1, len(prices)):
            diff = prices[i] - prices[i-1]
            gains.append(max(0.0, diff))
            losses.append(max(0.0, -diff))
            
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        
        rsi = []
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rsi.append(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
            
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi.append(100.0)
            else:
                rsi.append(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
        return [50.0] * period + rsi

    def calculate_vwap(self, candles: List[Dict[str, Any]]) -> List[float]:
        vwap = []
        cum_pv = 0.0
        cum_v = 0.0
        last_day = None
        for c in candles:
            # Daily reset
            dt = datetime.fromtimestamp(c["time"], timezone.utc)
            day = dt.date()
            if day != last_day:
                cum_pv = 0.0
                cum_v = 0.0
                last_day = day
            
            typical_price = (c["high"] + c["low"] + c["close"]) / 3.0
            volume = c["tick_volume"] if c["tick_volume"] > 0 else 1
            cum_pv += typical_price * volume
            cum_v += volume
            vwap.append(cum_pv / cum_v if cum_v > 0 else typical_price)
        return vwap

    def calculate_momentum(self, prices: List[float], period: int = 14) -> List[float]:
        mom = []
        for i in range(len(prices)):
            if i < period:
                mom.append(0.0)
            else:
                prev = prices[i-period]
                curr = prices[i]
                mom.append(((curr - prev) / prev) * 100.0 if prev > 0 else 0.0)
        return mom
