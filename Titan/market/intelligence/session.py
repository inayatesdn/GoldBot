from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

class SessionEngine:
    """
    Identifies current active session, Kill Zones, and calculates high/low ranges for:
    Today, Yesterday, Week, Month.
    Returns confidence, reason, state, and metrics.
    """
    
    @staticmethod
    def analyze(candles: List[Dict[str, Any]]) -> Dict[str, Any]:
        if len(candles) == 0:
            return {
                "confidence": 0.5,
                "reason": "No candle data available.",
                "state": "OFF_HOURS",
                "metrics": {}
            }
            
        # 1. Classify Current Active Session from Last Candle Timestamp
        last_time = datetime.fromtimestamp(candles[-1]["time"], timezone.utc)
        hour = last_time.hour
        minute = last_time.minute
        decimal_hour = hour + minute / 60.0
        
        # Kill Zones and Session Status
        active_sessions = []
        state = "Tokyo-London Overlap"
        
        # Sydney (22:00 - 07:00 UTC)
        if decimal_hour >= 22.0 or decimal_hour <= 7.0:
            active_sessions.append("Sydney")
        # Tokyo (00:00 - 09:00 UTC)
        if 0.0 <= decimal_hour <= 9.0:
            active_sessions.append("Tokyo")
        # London (08:00 - 17:00 UTC)
        if 8.0 <= decimal_hour <= 17.0:
            active_sessions.append("London")
        # New York (13:00 - 22:00 UTC)
        if 13.0 <= decimal_hour <= 22.0:
            active_sessions.append("New York")
            
        # Kill Zones
        kill_zone = "NONE"
        if 0.0 <= decimal_hour <= 4.0:
            kill_zone = "Asian Kill Zone"
        elif 7.0 <= decimal_hour <= 10.0:
            kill_zone = "London Kill Zone"
        elif 12.0 <= decimal_hour <= 15.0:
            kill_zone = "New York Kill Zone"
            
        sessions_str = "/".join(active_sessions)
        if kill_zone != "NONE":
            sessions_str += f" ({kill_zone})"
            
        # 2. Calculate Ranges (Today, Yesterday, Week, Month)
        today_start = datetime(last_time.year, last_time.month, last_time.day, tzinfo=timezone.utc)
        yesterday_start = today_start - timedelta(days=1)
        yesterday_end = today_start - timedelta(seconds=1)
        
        week_start = today_start - timedelta(days=last_time.weekday())
        month_start = datetime(last_time.year, last_time.month, 1, tzinfo=timezone.utc)
        
        today_high, today_low = -999999.0, 999999.0
        yesterday_high, yesterday_low = -999999.0, 999999.0
        weekly_high, weekly_low = -999999.0, 999999.0
        monthly_high, monthly_low = -999999.0, 999999.0
        
        for c in candles:
            c_time = datetime.fromtimestamp(c["time"], timezone.utc)
            ch, cl = c["high"], c["low"]
            
            # Today
            if c_time >= today_start:
                if ch > today_high: today_high = ch
                if cl < today_low: today_low = cl
            # Yesterday
            if yesterday_start <= c_time <= yesterday_end:
                if ch > yesterday_high: yesterday_high = ch
                if cl < yesterday_low: yesterday_low = cl
            # Week
            if c_time >= week_start:
                if ch > weekly_high: weekly_high = ch
                if cl < weekly_low: weekly_low = cl
            # Month
            if c_time >= month_start:
                if ch > monthly_high: monthly_high = ch
                if cl < monthly_low: monthly_low = cl
                
        # Clean defaults if not populated
        if today_high == -999999.0: today_high = candles[-1]["high"]
        if today_low == 999999.0: today_low = candles[-1]["low"]
        if yesterday_high == -999999.0: yesterday_high = today_high
        if yesterday_low == 999999.0: yesterday_low = today_low
        if weekly_high == -999999.0: weekly_high = today_high
        if weekly_low == 999999.0: weekly_low = today_low
        if monthly_high == -999999.0: monthly_high = today_high
        if monthly_low == 999999.0: monthly_low = today_low
        
        # 3. Form Confidence
        confidence = 0.50
        reason = f"Current active trading session: {sessions_str}."
        
        # London/NY Kill Zones are high-probability setups
        if kill_zone in ["London Kill Zone", "New York Kill Zone"]:
            confidence = 0.85
            reason = f"High probability session window: {kill_zone} is active."
        elif "London" in active_sessions or "New York" in active_sessions:
            confidence = 0.70
            reason = f"Standard volatility hours active: {sessions_str}."
            
        metrics = {
            "active_hours": decimal_hour,
            "sessions": active_sessions,
            "kill_zone": kill_zone,
            "today_high": today_high,
            "today_low": today_low,
            "yesterday_high": yesterday_high,
            "yesterday_low": yesterday_low,
            "weekly_high": weekly_high,
            "weekly_low": weekly_low,
            "monthly_high": monthly_high,
            "monthly_low": monthly_low
        }
        
        return {
            "confidence": float(confidence),
            "reason": reason,
            "state": sessions_str,
            "metrics": metrics
        }
