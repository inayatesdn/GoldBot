import numpy as np
import logging
from typing import Dict, List, Any

logger = logging.getLogger("Titan.TechnicalAnalysis")

class TechAnalysis:
    
    @staticmethod
    def calculate_ema(prices: np.ndarray, period: int = 20) -> np.ndarray:
        if len(prices) == 0:
            return np.array([])
        ema = np.zeros_like(prices)
        ema[0] = prices[0]
        alpha = 2.0 / (period + 1.0)
        for i in range(1, len(prices)):
            ema[i] = alpha * prices[i] + (1.0 - alpha) * ema[i-1]
        return ema

    @staticmethod
    def rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
        """Calculates Relative Strength Index."""
        if len(prices) < period + 1:
            return np.ones_like(prices) * 50.0
            
        deltas = np.diff(prices)
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period
        
        rs = up / (down if down != 0 else 0.0001)
        rsi = np.zeros_like(prices)
        rsi[period] = 100.0 - 100.0 / (1.0 + rs)
        
        for i in range(period + 1, len(prices)):
            delta = deltas[i - 1]
            if delta > 0:
                up_val = delta
                down_val = 0.0
            else:
                up_val = 0.0
                down_val = -delta
                
            up = (up * (period - 1) + up_val) / period
            down = (down * (period - 1) + down_val) / period
            
            rs = up / (down if down != 0 else 0.0001)
            rsi[i] = 100.0 - 100.0 / (1.0 + rs)
            
        return rsi

    @staticmethod
    def macd(prices: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
        """Calculates MACD Line, Signal Line, and Histogram."""
        ema_fast = TechAnalysis.calculate_ema(prices, fast)
        ema_slow = TechAnalysis.calculate_ema(prices, slow)
        if len(ema_fast) == 0 or len(ema_slow) == 0:
            return np.array([]), np.array([])
        macd_line = ema_fast - ema_slow
        macd_signal = TechAnalysis.calculate_ema(macd_line, signal)
        return macd_line, macd_signal

    @staticmethod
    def get_swings(highs: List[float], lows: List[float], window: int = 5):
        """Identifies pivot high and pivot low swing points."""
        swing_highs = []
        swing_lows = []
        
        for i in range(window, len(highs) - window):
            if all(highs[i] >= highs[i - j] for j in range(1, window + 1)) and \
               all(highs[i] >= highs[i + j] for j in range(1, window + 1)):
                swing_highs.append((i, highs[i]))
            if all(lows[i] <= lows[i - j] for j in range(1, window + 1)) and \
               all(lows[i] <= lows[i + j] for j in range(1, window + 1)):
                swing_lows.append((i, lows[i]))
                
        return swing_highs, swing_lows

    @staticmethod
    def find_fvgs(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Identifies Fair Value Gaps (FVG).
        A Bull FVG exists if Candle 1 High < Candle 3 Low.
        A Bear FVG exists if Candle 1 Low > Candle 3 High.
        """
        fvgs = []
        if len(candles) < 3:
            return fvgs
        start_idx = max(0, len(candles) - 30)
        for i in range(start_idx, len(candles) - 2):
            c1, c2, c3 = candles[i], candles[i+1], candles[i+2]
            
            if c1["high"] < c3["low"]:
                fvgs.append({
                    "type": "BULL",
                    "top": c3["low"],
                    "bottom": c1["high"],
                    "mitigated": False,
                    "candle_index": i + 1
                })
            elif c1["low"] > c3["high"]:
                fvgs.append({
                    "type": "BEAR",
                    "top": c1["low"],
                    "bottom": c3["high"],
                    "mitigated": False,
                    "candle_index": i + 1
                })
                
        # Check mitigation
        for fvg in fvgs:
            idx = fvg["candle_index"] + 1
            for k in range(idx, len(candles)):
                if fvg["type"] == "BULL" and candles[k]["low"] <= fvg["bottom"]:
                    fvg["mitigated"] = True
                elif fvg["type"] == "BEAR" and candles[k]["high"] >= fvg["top"]:
                    fvg["mitigated"] = True
                    
        return fvgs

    @staticmethod
    def find_order_blocks(candles: List[Dict[str, Any]], swings: tuple, window: int = 5) -> List[Dict[str, Any]]:
        """Finds Order Blocks (OB) in down or up expansions."""
        obs = []
        if len(candles) < window * 2:
            return obs
            
        start_idx = max(1, len(candles) - 30)
        for i in range(start_idx, len(candles) - 3):
            c1, c2 = candles[i], candles[i+1]
            
            if c1["close"] < c1["open"] and candles[i+1]["close"] > c1["open"]:
                obs.append({
                    "type": "BULL",
                    "top": max(c1["open"], c1["high"]),
                    "bottom": c1["low"],
                    "mitigated": False,
                    "candle_index": i
                })
            elif c1["close"] > c1["open"] and candles[i+1]["close"] < c1["open"]:
                obs.append({
                    "type": "BEAR",
                    "top": c1["high"],
                    "bottom": min(c1["open"], c1["low"]),
                    "mitigated": False,
                    "candle_index": i
                })
                    
        for ob in obs:
            idx = ob["candle_index"] + 2
            for k in range(idx, len(candles)):
                if ob["type"] == "BULL" and candles[k]["low"] <= ob["bottom"]:
                    ob["mitigated"] = True
                elif ob["type"] == "BEAR" and candles[k]["high"] >= ob["top"]:
                    ob["mitigated"] = True
                    
        return obs

    @staticmethod
    def analyze_tf_metrics(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculates indicators, trend, volatility, and structure for a single timeframe list."""
        closes = np.array([c["close"] for c in candles], dtype=float)
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        
        curr_price = closes[-1]
        
        # EMA trend
        ema20 = TechAnalysis.calculate_ema(closes, 20)
        ema50 = TechAnalysis.calculate_ema(closes, 50)
        
        trend = "BULLISH" if curr_price > ema20[-1] else "BEARISH"
        ema_aligned = (ema20[-1] > ema50[-1]) if trend == "BULLISH" else (ema20[-1] < ema50[-1])
        
        # RSI
        rsi_vals = TechAnalysis.rsi(closes, 14)
        curr_rsi = rsi_vals[-1]
        
        # MACD
        macd_line, macd_sig = TechAnalysis.macd(closes)
        macd_bull = False
        macd_bear = False
        if len(macd_line) > 1:
            macd_bull = macd_line[-1] > macd_sig[-1]
            macd_bear = macd_line[-1] < macd_sig[-1]
            
        # Structure swings
        sh, sl = TechAnalysis.get_swings(highs, lows, window=5)
        
        bos = False
        choch = False
        
        if trend == "BULLISH" and sh:
            last_sh = sh[-1][1]
            if curr_price > last_sh:
                bos = True
                if not ema_aligned:
                    choch = True
        elif trend == "BEARISH" and sl:
            last_sl = sl[-1][1]
            if curr_price < last_sl:
                bos = True
                if not ema_aligned:
                    choch = True
                    
        # Calculate ATR
        atr_14 = 0.0
        if len(closes) > 14:
            tr_vals = []
            for i in range(1, len(closes)):
                h = highs[i]
                l = lows[i]
                prev_c = closes[i-1]
                tr_vals.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
            atr_14 = float(np.mean(tr_vals[-14:]))

        # FVG and OB
        fvgs = TechAnalysis.find_fvgs(candles)
        obs = TechAnalysis.find_order_blocks(candles, (sh, sl))
        
        unmit_fvgs = [f for f in fvgs if not f["mitigated"] and f["type"] == ("BULL" if trend == "BULLISH" else "BEAR")]
        unmit_obs = [o for o in obs if not o["mitigated"] and o["type"] == ("BULL" if trend == "BULLISH" else "BEAR")]
        
        fvg_touched = False
        if unmit_fvgs:
            low_bound = min([f["bottom"] for f in unmit_fvgs])
            high_bound = max([f["top"] for f in unmit_fvgs])
            if low_bound <= curr_price <= high_bound:
                fvg_touched = True
                
        ob_touched = False
        if unmit_obs:
            low_bound = min([o["bottom"] for o in unmit_obs])
            high_bound = max([o["top"] for o in unmit_obs])
            if low_bound <= curr_price <= high_bound:
                ob_touched = True

        return {
            "trend": trend,
            "ema_aligned": ema_aligned,
            "rsi": curr_rsi,
            "macd_bullish": macd_bull,
            "macd_bearish": macd_bear,
            "bos": bos,
            "choch": choch,
            "fvg_touched": fvg_touched,
            "ob_touched": ob_touched,
            "unmit_fvgs": unmit_fvgs,
            "unmit_obs": unmit_obs,
            "swings": {"highs": sh, "lows": sl},
            "atr_14": atr_14
        }

    @staticmethod
    def analyze_multi_timeframe(m1_candles: List[Dict[str, Any]], 
                               m3_candles: List[Dict[str, Any]], 
                               m5_candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Runs institutional Multi-Timeframe confluences.
        M5 = Macro Trend, M3 = Confirmation, M1 = Execution.
        Only generates trade signals when agreement exists.
        """
        m5_analysis = TechAnalysis.analyze_tf_metrics(m5_candles)
        m3_analysis = TechAnalysis.analyze_tf_metrics(m3_candles)
        m1_analysis = TechAnalysis.analyze_tf_metrics(m1_candles)
        
        macro_trend = m5_analysis["trend"]
        conf_trend = m3_analysis["trend"]
        exec_trend = m1_analysis["trend"]
        
        trend_aligned = (macro_trend == conf_trend == exec_trend)
        
        # If trend is aligned, we calculate confluence weights
        score = 0
        reasons = []
        
        if trend_aligned:
            score += 30  # Trend Agreement base points
            reasons.append("Multi-timeframe trend alignment")
            
            # Subsystem indicators
            selected_exec = m1_analysis
            
            if selected_exec["ema_aligned"]:
                score += 15
                reasons.append("EMA moving average stack aligned")
                
            if macro_trend == "BULLISH":
                # Momentum confirmation
                if selected_exec["rsi"] < 40:
                    score += 15
                    reasons.append("Execution oversold accumulation")
                if selected_exec["macd_bullish"]:
                    score += 10
                    reasons.append("Execution MACD cross bullish")
                # Structure
                if selected_exec["bos"]:
                    score += 15
                    reasons.append("Bullish break of structure (BOS)")
                if selected_exec["choch"]:
                    score += 15
                    reasons.append("Bullish structural change of character (CHoCH)")
                # Touch points
                if selected_exec["ob_touched"]:
                    score += 20
                    reasons.append("Reacting inside bullish order block zone")
                if selected_exec["fvg_touched"]:
                    score += 15
                    reasons.append("Filling execution Fair Value Gap inefficiency")
            else: # BEARISH
                if selected_exec["rsi"] > 60:
                    score += 15
                    reasons.append("Execution overbought distribution")
                if selected_exec["macd_bearish"]:
                    score += 10
                    reasons.append("Execution MACD cross bearish")
                if selected_exec["bos"]:
                    score += 15
                    reasons.append("Bearish break of structure (BOS)")
                if selected_exec["choch"]:
                    score += 15
                    reasons.append("Bearish structural change of character (CHoCH)")
                if selected_exec["ob_touched"]:
                    score += 20
                    reasons.append("Reacting inside bearish order block zone")
                if selected_exec["fvg_touched"]:
                    score += 15
                    reasons.append("Filling execution Fair Value Gap inefficiency")
        else:
            reasons.append(f"Trend conflict: M5={macro_trend}, M3={conf_trend}, M1={exec_trend}")
            
        confluence_payload = {
            "trend_aligned": trend_aligned,
            "macro_trend": macro_trend,
            "conf_trend": conf_trend,
            "exec_trend": exec_trend,
            "m5_metrics": m5_analysis,
            "m3_metrics": m3_analysis,
            "m1_metrics": m1_analysis,
            "score": score,
            "reasons": reasons
        }
        return confluence_payload
