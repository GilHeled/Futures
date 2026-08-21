"""Daily/Weekly resampling follows the CME session day, and D/W stay prefix-stable.

Daily bar for trade date T must span 18:00 ET (T-1) -> 17:00 ET (T) and must NOT merge the
17:00-18:00 maintenance halt or the prior/next session. Weekly spans Sun 18:00 -> Fri 17:00.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.market.bar_builder import BarBuilder

ET = ZoneInfo("America/New_York")


def _stream(start, minutes):
    """`minutes` consecutive 1m bars from ET `start`; skips the daily maintenance halt
    (17:00-18:00 ET) and weekends so we only feed real session minutes."""
    from ict_live.market.calendar import Calendar
    cal = Calendar()
    out, t, px = [], start, 20000.0
    made = 0
    while made < minutes:
        if cal.is_open(t):
            o = px; c = px + (1.0 if made % 2 == 0 else -1.0)
            out.append(Bar("1m", t, t + timedelta(minutes=1), o, max(o, c) + 0.5, min(o, c) - 0.5, c, 5.0))
            px = c; made += 1
        t = t + timedelta(minutes=1)
    return out


def test_daily_bucket_follows_session_day():
    # Start Monday 2026-06-01 18:00 ET (== Tuesday's session open). Feed ~2 sessions.
    bb = BarBuilder(("D",))
    stream = _stream(datetime(2026, 6, 1, 18, 0, tzinfo=ET), 60 * 30)
    closed = [x for b in stream for x in bb.add_1m(b)]
    dbars = [x for x in closed if x.timeframe == "D"]
    assert dbars, "expected at least one closed daily bar"
    d0 = dbars[0]
    # First completed session day = Tuesday 2026-06-02: 18:00(Mon) -> 17:00(Tue)
    assert d0.open_time == datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    assert d0.close_time == datetime(2026, 6, 2, 17, 0, tzinfo=ET)
    assert not d0.forming


def test_daily_does_not_span_maintenance():
    # The forming daily bar right after a session opens must start at 18:00, not carry the
    # prior session's close through the 17:00-18:00 gap.
    bb = BarBuilder(("D",))
    stream = _stream(datetime(2026, 6, 1, 18, 0, tzinfo=ET), 60 * 26)  # ~1 session + into next
    for b in stream:
        bb.add_1m(b)
    f = bb.forming("D")
    assert f is not None
    assert f.open_time == datetime(2026, 6, 2, 18, 0, tzinfo=ET)       # next session's open


def test_weekly_bucket_bounds():
    bb = BarBuilder(("W",))
    # Feed from Sunday 2026-05-31 18:00 ET (open of the 06-01..05 week) through Fri close.
    stream = _stream(datetime(2026, 5, 31, 18, 0, tzinfo=ET), 60 * 24 * 6)
    closed = [x for b in stream for x in bb.add_1m(b)]
    wbars = [x for x in closed if x.timeframe == "W"]
    assert wbars
    w0 = wbars[0]
    assert w0.open_time == datetime(2026, 5, 31, 18, 0, tzinfo=ET)
    assert w0.close_time == datetime(2026, 6, 5, 17, 0, tzinfo=ET)


def test_prefix_stability_with_D_and_W():
    stream = _stream(datetime(2026, 6, 1, 18, 0, tzinfo=ET), 60 * 30)
    tfs = ("15m", "1H", "D", "W")
    full = BarBuilder(tfs)
    cumulative, running = [], []
    for b in stream:
        running += full.add_1m(b)
        cumulative.append(list(running))
    for k in range(0, len(stream), 7):     # sample prefixes (full loop is O(n^2))
        pbb = BarBuilder(tfs)
        pc = []
        for b in stream[: k + 1]:
            pc += pbb.add_1m(b)
        assert pc == cumulative[k], f"look-ahead/nondeterminism at k={k}"
