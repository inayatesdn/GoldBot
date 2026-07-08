from datetime import datetime, timezone

class SessionManager:
    @staticmethod
    def get_current_sessions(utc_time: datetime = None):
        """
        Calculates active global trading sessions based on current UTC time.
        Sydney: 21:00 - 06:00 UTC
        Tokyo: 00:00 - 09:00 UTC
        London: 08:00 - 16:00 UTC
        New York: 13:00 - 21:00 UTC
        """
        if utc_time is None:
            utc_time = datetime.now(timezone.utc)
            
        hour = utc_time.hour
        minute = utc_time.minute
        decimal_hour = hour + (minute / 60.0)
        
        active = []
        
        # Sydney Hour Checks (21:00 - 06:00 UTC)
        if decimal_hour >= 21.0 or decimal_hour < 6.0:
            active.append("Sydney")
            
        # Tokyo Hour Checks (00:00 - 09:00 UTC)
        if 0.0 <= decimal_hour < 9.0:
            active.append("Tokyo")
            
        # London Hour Checks (08:00 - 17:00 UTC approx - standard winter hours)
        if 8.0 <= decimal_hour < 16.0:
            active.append("London")
            
        # New York Hour Checks (13:00 - 21:00 UTC)
        if 13.0 <= decimal_hour < 21.0:
            active.append("New York")
            
        # Overlaps & Kill Zones
        overlaps = []
        if "London" in active and "New York" in active:
            overlaps.append("London-New York Overlap")
        if "Sydney" in active and "Tokyo" in active:
            overlaps.append("Sydney-Tokyo Overlap")
            
        kill_zones = []
        # London Open Kill Zone: 07:30 - 09:30 UTC
        if 7.5 <= decimal_hour < 9.5:
            kill_zones.append("London Open Kill Zone")
        # NY Open Kill Zone: 12:30 - 14:30 UTC
        if 12.5 <= decimal_hour < 14.5:
            kill_zones.append("NY Open Kill Zone")
        # London Close Kill Zone: 15:30 - 17:00 UTC (15:30 - 17:00 UTC)
        if 15.5 <= decimal_hour < 17.0:
            kill_zones.append("London Close Kill Zone")
            
        return {
            "utc_time": utc_time.strftime('%Y-%m-%d %H:%M:%S'),
            "active_sessions": active,
            "overlaps": overlaps,
            "kill_zones": kill_zones,
            "session_desc": ", ".join(active) if active else "Slow Market"
        }
