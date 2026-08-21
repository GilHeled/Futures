"""Session/killzone membership windows (ET, DST-safe), incl. Asia midnight-wrap."""
from datetime import datetime
from zoneinfo import ZoneInfo

from ict_live import config as C
from ict_live.market import sessions as S

ET = ZoneInfo("America/New_York")


def _et(hh, mm, d=2):
    return datetime(2026, 6, d, hh, mm, tzinfo=ET)


def test_config_windows_present():
    for name in ("asia", "london_active", "ny_am", "ny_pm"):
        assert name in C.SESSIONS


def test_ny_am_membership_half_open():
    s, e = C.SESSIONS["ny_am"]
    assert S.in_session(_et(s.hour, s.minute), "ny_am")          # inclusive start
    assert not S.in_session(_et(e.hour, e.minute), "ny_am")      # exclusive end
    assert S.killzone(_et(s.hour, s.minute)) == "ny_am"


def test_asia_wraps_midnight():
    # Asia 18:00 -> 00:00 (same evening into midnight). 20:00 in; 01:00 out.
    assert S.in_session(_et(20, 0), "asia")
    assert not S.in_session(_et(1, 0), "asia")
    assert S.killzone(_et(20, 0)) is None    # asia is reference, not a trading killzone


def test_active_windows_and_killzone_exclusivity():
    # a mid-afternoon NY-PM time is a killzone; a dead-zone time is not
    pm_s, _ = C.SESSIONS["ny_pm"]
    assert "ny_pm" in S.active_windows(_et(pm_s.hour, pm_s.minute))
    assert S.killzone(_et(pm_s.hour, pm_s.minute)) == "ny_pm"
    assert S.killzone(_et(6, 0)) is None     # 06:00 ET: between london_active and ny_am


def test_dst_membership_is_wall_clock():
    # ny_am at 09:00 ET must be a member in both summer and winter (wall-clock, not UTC)
    s, _ = C.SESSIONS["ny_am"]
    summer = datetime(2026, 7, 1, s.hour, s.minute, tzinfo=ET)
    winter = datetime(2026, 1, 15, s.hour, s.minute, tzinfo=ET)
    assert S.in_session(summer, "ny_am") and S.in_session(winter, "ny_am")
