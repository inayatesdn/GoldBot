import numpy as np
from typing import List, Dict, Any

def calculate_sma(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if len(prices) > 0 else 0.0
    return float(np.mean(prices[-period:]))

def calculate_ema(prices: List[float], period: int) -> List[float]:
    if len(prices) == 0:
        return []
    ema = np.zeros(len(prices))
    ema[0] = prices[0]
    alpha = 2.0 / (period + 1.0)
    for i in range(1, len(prices)):
        ema[i] = alpha * prices[i] + (1.0 - alpha) * ema[i-1]
    return ema.tolist()

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 0.0
    
    tr_list = []
    for i in range(1, len(closes)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i-1])
        tr3 = abs(lows[i] - closes[i-1])
        tr = max(tr1, tr2, tr3)
        tr_list.append(tr)
        
    atr = tr_list[0]
    alpha = 1.0 / period
    for tr in tr_list[1:]:
        atr = alpha * tr + (1.0 - alpha) * atr
    return float(atr)

def calculate_rsi(closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < period + 1:
        return [50.0] * len(closes)
        
    closes_arr = np.array(closes, dtype=float)
    diff = np.diff(closes_arr)
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    
    rsi_vals = [50.0] * (period + 1)
    
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    
    # First RSI
    if avg_loss == 0:
        rsi_vals[-1] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi_vals[-1] = float(100.0 - (100.0 / (1.0 + rs)))
        
    # Subsequent values
    for i in range(period, len(diff)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(float(100.0 - (100.0 / (1.0 + rs))))
            
    # Pad to match original closes length
    while len(rsi_vals) < len(closes):
        rsi_vals.insert(0, 50.0)
    return rsi_vals

def calculate_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    if len(closes) < (period * 2):
        return 25.0
        
    high_arr = np.array(highs, dtype=float)
    low_arr = np.array(lows, dtype=float)
    close_arr = np.array(closes, dtype=float)
    
    upmoves = high_arr[1:] - high_arr[:-1]
    downmoves = low_arr[:-1] - low_arr[1:]
    
    plus_dm = np.where((upmoves > downmoves) & (upmoves > 0), upmoves, 0.0)
    minus_dm = np.where((downmoves > upmoves) & (downmoves > 0), downmoves, 0.0)
    
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(high_arr[i] - low_arr[i], abs(high_arr[i] - close_arr[i-1]), abs(low_arr[i] - close_arr[i-1]))
        tr_list.append(tr)
        
    tr_arr = np.array(tr_list)
    
    tr_smooth = np.zeros(len(tr_arr) - period + 1)
    plus_dm_smooth = np.zeros(len(plus_dm) - period + 1)
    minus_dm_smooth = np.zeros(len(minus_dm) - period + 1)
    
    tr_smooth[0] = np.sum(tr_arr[:period])
    plus_dm_smooth[0] = np.sum(plus_dm[:period])
    minus_dm_smooth[0] = np.sum(minus_dm[:period])
    
    for idx in range(1, len(tr_smooth)):
        tr_smooth[idx] = tr_smooth[idx-1] - (tr_smooth[idx-1]/period) + tr_arr[period - 1 + idx]
        plus_dm_smooth[idx] = plus_dm_smooth[idx-1] - (plus_dm_smooth[idx-1]/period) + plus_dm[period - 1 + idx]
        minus_dm_smooth[idx] = minus_dm_smooth[idx-1] - (minus_dm_smooth[idx-1]/period) + minus_dm[period - 1 + idx]
        
    tr_smooth = np.where(tr_smooth == 0, 0.0001, tr_smooth)
    
    plus_di = 100 * (plus_dm_smooth / tr_smooth)
    minus_di = 100 * (minus_dm_smooth / tr_smooth)
    
    di_sum = plus_di + minus_di
    di_sum = np.where(di_sum == 0, 0.0001, di_sum)
    dx = 100 * (np.abs(plus_di - minus_di) / di_sum)
    
    adx = np.zeros(len(dx) - period + 1)
    adx[0] = np.mean(dx[:period])
    for idx in range(1, len(adx)):
        adx[idx] = (adx[idx-1] * (period - 1) + dx[period - 1 + idx]) / period
        
    return float(adx[-1])

def calculate_macd(closes: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, float]:
    if len(closes) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    
    ema_fast = calculate_ema(closes, fast)
    ema_slow = calculate_ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = calculate_ema(macd_line, signal)
    
    return {
        "macd": macd_line[-1],
        "signal": signal_line[-1],
        "histogram": macd_line[-1] - signal_line[-1]
    }
