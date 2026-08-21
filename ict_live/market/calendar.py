"""CME Globex equity-index futures trading calendar (ET, DST-safe).

Session model (ES/NQ/MES/MNQ family):
  * Globex runs Sunday 18:00 ET -> Friday 17:00 ET.
  * Daily maintenance halt 17:00-18:00 ET (Mon-Thu) separates one session day from the next.
  * TRADE-DATE convention: the session that OPENS at 18:00 ET belongs to the NEXT calendar
    day. So a bar timestamped Sunday 20:00 ET is part of MONDAY's session; Monday 20:00 ET
    is part of TUESDAY's session; a bar at Monday 09:30 ET is part of Monday's session.
  * Holidays fully closed; half-days close early (e.g. 13:00 ET).

DST is handled by constructing ET wall-clock datetimes directly via zoneinfo (each resolves
its own UTC offset) — never by adding a fixed offset. Note: US DST switches occur ~Sunday
02:00 ET while this market is closed, so no OPEN intraday bucket spans a transition.

The holiday / half-day tables are DATA (a maintained table), injected at construction;
a documented 2025-2026 subset is the default. The structural logic (session-day, maintenance,
weekend, DST, bounds, gap) is what the tests pin down — not the exact holiday list.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

SESSION_OPEN = time(18, 0)     # ET; session opens previous evening
SESSION_CLOSE = time(17, 0)    # ET; session closes on the trade date
EARLY_CLOSE_DEFAULT = time(13, 0)

# Documented default subset (CME equity-index). MAINTAIN as a data table; injectable.
_DEFAULT_HOLIDAYS = {
    date(2025, 1, 1), date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4),
    date(2025, 9, 1), date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3),
    date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
}
_DEFAULT_HALF_DAYS = {
    date(2025, 7, 3): time(13, 0), date(2025, 11, 28): time(13, 0), date(2025, 12, 24): time(13, 0),
    date(2026, 11, 27): time(13, 0), date(2026, 12, 24): time(13, 0),
}


class Calendar:
    def __init__(self, holidays=None, half_days=None):
        self.holidays = set(_DEFAULT_HOLIDAYS if holidays is None else holidays)
        self.half_days = dict(_DEFAULT_HALF_DAYS if half_days is None else half_days)

    # ---- core ----
    def session_day(self, dt: datetime) -> date | None:
        """CME trade date for an ET timestamp, or None if in maintenance/weekend/holiday."""
        dt = dt.astimezone(ET)
        t = dt.timetz().replace(tzinfo=None)
        wd = dt.weekday()  # Mon=0 .. Sun=6
        # maintenance halt 17:00-18:00 (any day) => no session
        if SESSION_CLOSE <= t < SESSION_OPEN:
            return None
        # trade date: >=18:00 belongs to next calendar day; <17:00 to same calendar day
        sd = dt.date() + timedelta(days=1) if t >= SESSION_OPEN else dt.date()
        # weekend: no session with trade date Sat/Sun; Sat has none; Fri-evening (>=18:00)
        # would map to Sat -> closed; Sun-daytime (<17:00) maps to Sun -> closed.
        if sd.weekday() >= 5:   # Sat(5)/Sun(6) trade dates do not exist
            return None
        if sd in self.holidays:
            return None
        if sd in self.half_days and t >= self.half_days[sd] and t < SESSION_CLOSE:
            return None          # after the half-day early close
        return sd

    def is_open(self, dt: datetime) -> bool:
        return self.session_day(dt) is not None

    def day_bounds(self, session_date: date) -> tuple[datetime, datetime]:
        """[open, close) for a trade date: 18:00 ET the prior calendar evening -> 17:00 ET
        (or the half-day early close) on the trade date."""
        start = datetime.combine(session_date - timedelta(days=1), SESSION_OPEN, ET)
        close_t = self.half_days.get(session_date, SESSION_CLOSE)
        end = datetime.combine(session_date, close_t, ET)
        return start, end

    def week_bounds(self, dt: datetime) -> tuple[datetime, datetime]:
        """Trading week [Sun 18:00 ET, Fri 17:00 ET) containing dt's session."""
        dt = dt.astimezone(ET)
        # anchor on the trade date if in-session, else nearest calendar date
        sd = self.session_day(dt) or dt.date()
        # walk back to the Monday trade date of this week, then Sunday-evening open
        monday = sd - timedelta(days=sd.weekday())        # Mon of sd's ISO week
        start = datetime.combine(monday - timedelta(days=1), SESSION_OPEN, ET)  # Sun 18:00
        friday = monday + timedelta(days=4)
        end = datetime.combine(friday, SESSION_CLOSE, ET)
        return start, end

    def count_open_minutes(self, start: datetime, end: datetime) -> int:
        """Number of OPEN 1-minute slots in [start, end) (skips maintenance/weekend/holiday).
        Used to size a detected gap. Bounded scan; caps at ~14 days to stay finite."""
        start = start.astimezone(ET); end = end.astimezone(ET)
        n, dt, cap = 0, start, 14 * 24 * 60
        while dt < end and cap > 0:
            if self.is_open(dt):
                n += 1
            dt = dt + timedelta(minutes=1); cap -= 1
        return n

    def next_expected_open_minute(self, prev_close: datetime) -> datetime:
        """First minute that should carry a bar at/after `prev_close`, skipping closed
        periods (maintenance/weekend/holiday). Used for gap detection."""
        dt = prev_close.astimezone(ET)
        for _ in range(4 * 24 * 60):     # bounded scan (<=4 days of minutes)
            if self.is_open(dt):
                return dt
            dt = dt + timedelta(minutes=1)
        return dt
