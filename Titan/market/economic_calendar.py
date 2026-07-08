import datetime
import calendar
import logging
from Titan.config.config import NEWS_LOCK_MINUTES_PRE, NEWS_LOCK_MINUTES_POST

logger = logging.getLogger("Titan.EconomicCalendar")

class EconomicCalendar:
    
    @staticmethod
    def get_first_friday(year, month):
        """Finds the first Friday of the month."""
        c = calendar.monthcalendar(year, month)
        first_week = c[0]
        second_week = c[1]
        
        # Friday index is 4 in python calendar (Monday=0, Sunday=6)
        if first_week[4] != 0:
            return first_week[4]
        else:
            return second_week[4]

    @staticmethod
    def get_second_wednesday(year, month):
        """Finds the second Wednesday of the month."""
        c = calendar.monthcalendar(year, month)
        wednesdays = []
        for week in c:
            if week[2] != 0:
                wednesdays.append(week[2])
        if len(wednesdays) >= 2:
            return wednesdays[1]
        return wednesdays[0]

    @staticmethod
    def get_scheduled_high_impact_news(date_to_check: datetime.date = None):
        """
        Calculates high-impact news releases for USD/Gold based on calendar rules.
        All times represented in UTC timezone.
        """
        if date_to_check is None:
            date_to_check = datetime.datetime.now(datetime.timezone.utc).date()
            
        events = []
        year = date_to_check.year
        month = date_to_check.month
        
        # 1. Non-Farm Payrolls (NFP): First Friday of the Month at 13:30 UTC
        nfp_day = EconomicCalendar.get_first_friday(year, month)
        if nfp_day == date_to_check.day:
            events.append({
                "time": datetime.time(13, 30),
                "title": "US Non-Farm Payrolls (NFP)",
                "impact": "HIGH",
                "currency": "USD"
            })
            
        # 2. US Consumer Price Index (CPI): Second Wednesday of the Month at 13:30 UTC
        cpi_day = EconomicCalendar.get_second_wednesday(year, month)
        if cpi_day == date_to_check.day:
            events.append({
                "time": datetime.time(13, 30),
                "title": "US Core CPI MoM / YoY",
                "impact": "HIGH",
                "currency": "USD"
            })
            events.append({
                "time": datetime.time(13, 30),
                "title": "US Core PPI MoM",
                "impact": "HIGH",
                "currency": "USD"
            })
            
        # 3. FOMC Interest Rate Decision: Simulated on specific Wednesdays (roughly every 6 weeks)
        # We can map standard 2026 FOMC dates (Jan 28, Mar 18, May 6, June 17, July 29, Sept 16, Oct 28, Dec 16)
        fomc_dates = [
            (2026, 1, 28), (2026, 3, 18), (2026, 5, 6), (2026, 6, 17),
            (2026, 7, 29), (2026, 9, 16), (2026, 10, 28), (2026, 12, 16)
        ]
        
        for f_year, f_month, f_day in fomc_dates:
            if date_to_check.year == f_year and date_to_check.month == f_month and date_to_check.day == f_day:
                events.append({
                    "time": datetime.time(18, 0),
                    "title": "US FOMC Interest Rate Decision",
                    "impact": "HIGH",
                    "currency": "USD"
                })
                events.append({
                    "time": datetime.time(18, 30),
                    "title": "US FOMC Press Conference Summary",
                    "impact": "HIGH",
                    "currency": "USD"
                })
                break
                
        # 4. Standard retail sales / FED speeches (custom additions or regular indicators)
        # Add a placeholder for general central bank speeches on Thursday afternoons
        if date_to_check.weekday() == 3: # Thursday
            events.append({
                "time": datetime.time(14, 0),
                "title": "Federal Reserve Chair Speech",
                "impact": "MEDIUM",
                "currency": "USD"
            })
            
        # Sort events by time
        events.sort(key=lambda x: x["time"])
        return events

    @staticmethod
    def check_news_lock(utc_time: datetime.datetime = None):
        """
        Verifies if the current time is under a news block restrictions.
        Returns (is_locked, minutes_remaining, active_event_title)
        """
        if utc_time is None:
            utc_time = datetime.datetime.now(datetime.timezone.utc)
            
        events = EconomicCalendar.get_scheduled_high_impact_news(utc_time.date())
        current_dt = utc_time
        
        for ev in events:
            # Combine the current date with the event time
            ev_dt = datetime.datetime.combine(utc_time.date(), ev["time"], tzinfo=datetime.timezone.utc)
            
            # Difference in minutes
            diff_seconds = (current_dt - ev_dt).total_seconds()
            diff_minutes = diff_seconds / 60.0
            
            is_locked = False
            
            # If current time is before event, check if details fall in NEWS_LOCK_MINUTES_PRE
            if diff_minutes < 0 and abs(diff_minutes) <= NEWS_LOCK_MINUTES_PRE:
                is_locked = True
                remaining = abs(diff_minutes)
                
            # If current time is after event, check if details fall in NEWS_LOCK_MINUTES_POST
            elif diff_minutes >= 0 and diff_minutes <= NEWS_LOCK_MINUTES_POST:
                is_locked = True
                remaining = NEWS_LOCK_MINUTES_POST - diff_minutes
                
            if is_locked:
                return True, round(remaining, 1), ev["title"]
                
        return False, 0.0, ""
