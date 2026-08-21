"""Objective liquidity pools: PDH/PDL, PWH/PWL, completed-session H/L — all causal."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure.liquidity import LiquidityRegistry

ET = ZoneInfo("America/New_York")


def _bar(tf, ot, dur_min, o, h, l, c):
    return Bar(tf, ot, ot + timedelta(minutes=dur_min), o, h, l, c, 1.0)


def test_prior_day_and_week_pools():
    reg = LiquidityRegistry()
    d = _bar("D", datetime(2026, 6, 1, 18, 0, tzinfo=ET), 60 * 23,
             20000, 20120, 19950, 20080)
    reg.on_daily_close(d)
    w = _bar("W", datetime(2026, 5, 31, 18, 0, tzinfo=ET), 60 * 24 * 5,
             20000, 20300, 19800, 20200)
    reg.on_weekly_close(w)
    by = {p.name: p for p in reg.pools()}
    assert by["PDH"].price == 20120 and by["PDL"].price == 19950
    assert by["PWH"].price == 20300 and by["PWL"].price == 19800
    assert all(p.erl for p in reg.pools())


def _one_min_series(start, minutes, base=20000.0, wiggle=1.0):
    out, t, px = [], start, base
    for i in range(minutes):
        o = px; c = px + (wiggle if i % 2 == 0 else -wiggle)
        out.append(_bar("1m", t, 1, o, max(o, c) + 0.5, min(o, c) - 0.5, c))
        px = c; t = t + timedelta(minutes=1)
    return out


def test_session_pool_finalized_after_window_ends():
    # NY-AM window (08:30-11:00 ET). Feed 08:30..11:05 so the window completes, plus a spike.
    reg = LiquidityRegistry()
    stream = _one_min_series(datetime(2026, 6, 2, 8, 30, tzinfo=ET), 160)
    # inject a clear session high at 09:00 and low at 10:00
    for b in stream:
        if b.open_time.hour == 9 and b.open_time.minute == 0:
            b = Bar("1m", b.open_time, b.close_time, b.open, 20099.0, b.low, b.close, 1.0)
        if b.open_time.hour == 10 and b.open_time.minute == 0:
            b = Bar("1m", b.open_time, b.close_time, b.open, b.high, 19901.0, b.close, 1.0)
        reg.on_1m(b)
    by = {p.name: p for p in reg.pools()}
    assert "NY_AM_H" in by and "NY_AM_L" in by
    assert by["NY_AM_H"].price == 20099.0 and by["NY_AM_L"].price == 19901.0
    assert by["NY_AM_H"].source == "session:ny_am"


def test_session_pool_not_emitted_while_forming():
    # feed only up to 09:30 (still inside NY-AM) -> no finalized NY_AM pool yet (causal)
    reg = LiquidityRegistry()
    for b in _one_min_series(datetime(2026, 6, 2, 8, 30, tzinfo=ET), 60):
        reg.on_1m(b)
    assert not any(p.name.startswith("NY_AM") for p in reg.pools())
