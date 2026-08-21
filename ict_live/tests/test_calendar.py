"""CME session-day / DST / holiday / half-day / weekend / maintenance / gap logic.

Pins the STRUCTURAL rules (not the exact holiday list). Trade-date convention: the session
opening 18:00 ET belongs to the NEXT calendar day.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.calendar import Calendar, ET

cal = Calendar()


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_trade_date_convention_evening_belongs_to_next_day():
    # Monday 2026-06-01 20:00 ET -> Tuesday's session
    assert cal.session_day(_et(2026, 6, 1, 20, 0)) == date(2026, 6, 2)
    # Monday 09:30 ET -> Monday's session
    assert cal.session_day(_et(2026, 6, 1, 9, 30)) == date(2026, 6, 1)
    # Exactly 18:00 rolls to next day; 17:59 is maintenance (None)
    assert cal.session_day(_et(2026, 6, 1, 18, 0)) == date(2026, 6, 2)
    assert cal.session_day(_et(2026, 6, 1, 17, 30)) is None


def test_maintenance_halt_is_closed():
    for hh, mm in [(17, 0), (17, 30), (17, 59)]:
        assert cal.session_day(_et(2026, 6, 2, hh, mm)) is None
    assert cal.is_open(_et(2026, 6, 2, 16, 59))
    assert cal.is_open(_et(2026, 6, 2, 18, 0))


def test_weekend_closed_but_sunday_evening_opens_monday():
    # Saturday: fully closed
    assert cal.session_day(_et(2026, 6, 6, 12, 0)) is None
    # Sunday daytime: closed (Sun trade date doesn't exist)
    assert cal.session_day(_et(2026, 6, 7, 12, 0)) is None
    # Sunday 18:00 ET -> Monday session
    assert cal.session_day(_et(2026, 6, 7, 18, 0)) == date(2026, 6, 8)
    # Friday 17:00 ET onward: closed for the week
    assert cal.session_day(_et(2026, 6, 5, 17, 0)) is None


def test_holiday_and_half_day():
    # 2026-07-03 is a holiday (full close) in the default table
    assert cal.session_day(_et(2026, 7, 3, 10, 0)) is None
    # 2026-11-27 half-day closes 13:00 ET
    assert cal.session_day(_et(2026, 11, 27, 12, 0)) == date(2026, 11, 27)
    assert cal.session_day(_et(2026, 11, 27, 13, 0)) is None
    _, end = cal.day_bounds(date(2026, 11, 27))
    assert end == _et(2026, 11, 27, 13, 0)


def test_day_bounds_span_prior_evening_to_close():
    start, end = cal.day_bounds(date(2026, 6, 2))
    assert start == _et(2026, 6, 1, 18, 0)      # prior calendar evening
    assert end == _et(2026, 6, 2, 17, 0)


def test_week_bounds_sun_to_fri():
    # any timestamp in the week of 2026-06-01..05
    start, end = cal.week_bounds(_et(2026, 6, 3, 10, 0))
    assert start == _et(2026, 5, 31, 18, 0)     # Sunday 18:00 ET
    assert end == _et(2026, 6, 5, 17, 0)        # Friday 17:00 ET


def test_dst_bounds_use_correct_offsets():
    # A summer (EDT, -04:00) and a winter (EST, -05:00) session must both anchor to
    # local 18:00 / 17:00 ET wall-clock, not a fixed UTC offset.
    s_summer, e_summer = cal.day_bounds(date(2026, 7, 1))
    s_winter, e_winter = cal.day_bounds(date(2026, 1, 15))
    assert s_summer.utcoffset() == timedelta(hours=-4)
    assert s_winter.utcoffset() == timedelta(hours=-5)
    assert e_summer.hour == 17 and e_winter.hour == 17


def test_next_expected_open_skips_maintenance_and_weekend():
    # last bar closes at Mon 17:00 ET (close); next expected open is 18:00 ET same evening
    nxt = cal.next_expected_open_minute(_et(2026, 6, 1, 17, 0))
    assert nxt == _et(2026, 6, 1, 18, 0)
    # Friday 17:00 close -> next open is Sunday 18:00 ET
    nxt2 = cal.next_expected_open_minute(_et(2026, 6, 5, 17, 0))
    assert nxt2 == _et(2026, 6, 7, 18, 0)
