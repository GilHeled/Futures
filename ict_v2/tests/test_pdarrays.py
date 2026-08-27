"""Course PD-array context detectors: NWOG (Lesson 13), ORG (Lesson 14)."""
from datetime import datetime, timedelta, timezone

from ict_live.market.bar import Bar
from ict_v2 import pdarrays

_UTC = timezone.utc


def _bar(t, o, h, l, c, dur_min=240):
    return Bar("4H", t, t + timedelta(minutes=dur_min), o, h, l, c, 100.0)


def _week(start, closes, step_h=4):
    """A run of contiguous 4H bars at `closes` prices, 4h apart, starting at `start`."""
    bars, t = [], start
    prevc = closes[0]
    for c in closes:
        o = prevc
        bars.append(_bar(t, o, max(o, c) + 1, min(o, c) - 1, c))
        prevc = c
        t = t + timedelta(hours=step_h)
    return bars, t


def test_nwog_detects_weekend_gap_and_midpoint():
    """Lesson 13: NWOG = gap between the last price before the weekend and the new-week open;
    boundaries + 50% midpoint; S/R + magnet (not liquidity). A >24h bar gap marks the weekly reopen."""
    t0 = datetime(2026, 6, 1, 14, 0, tzinfo=_UTC)              # week 1
    w1, tend = _week(t0, [100, 101, 100, 100])                # Friday close = 100
    # weekend: next bar opens 48h later at 105 (gap UP) → NWOG top=105 bottom=100 mid=102.5
    t_open = w1[-1].close_time + timedelta(hours=48)
    w2, _ = _week(t_open, [105, 106, 107, 106])               # stays above 100 → NOT closed
    got = pdarrays.nwogs(w1 + w2)
    assert len(got) == 1
    g = got[0]
    assert g["top"] == 105.0 and g["bottom"] == 100.0 and g["mid"] == 102.5
    assert g["closed"] is False                               # price never closed back through 100


def test_nwog_closed_when_rebalanced():
    """'Closed' = a later bar's body closes back through the pre-weekend price (Lesson 12 convention)."""
    t0 = datetime(2026, 6, 1, 14, 0, tzinfo=_UTC)
    w1, _ = _week(t0, [100, 101, 100, 100])
    t_open = w1[-1].close_time + timedelta(hours=48)
    w2, _ = _week(t_open, [105, 104, 102, 99])                # last bar closes 99 ≤ 100 → rebalanced
    got = pdarrays.nwogs(w1 + w2)
    assert len(got) == 1 and got[0]["closed"] is True


def test_nwog_keeps_three_plus_old_unclosed():
    """The course marks THREE; an UNCLOSED gap older than three weeks is additionally retained.
    A `week` here = two contiguous 4H bars starting on `day`; weeks are placed weeks apart so the
    inter-week jump is the (>24h) weekly-open gap, with realistic ages."""
    base_day = datetime(2026, 4, 1, 14, 0, tzinfo=_UTC)

    def week(day_offset, open_px, close_px):
        t = base_day + timedelta(days=day_offset)
        b1 = _bar(t, open_px, max(open_px, close_px) + 1, min(open_px, close_px) - 1, close_px)
        b2 = _bar(t + timedelta(hours=4), close_px, close_px + 1, close_px - 1, close_px)
        return [b1, b2]

    # gap A (oldest, day 0→7): 100→200 gap-up, and price never returns ≤100 → stays UNCLOSED, gets old
    bars = week(0, 100, 100) + week(7, 200, 200) + week(21, 210, 210) \
        + week(28, 220, 220) + week(35, 230, 230)              # gaps at days 7,21,28,35; gap A (day7) → 4wk old
    got = pdarrays.nwogs(bars, keep=3)
    ages = [g["age_weeks"] for g in got]
    assert len(got) == 4                                       # 3 most-recent + 1 retained old-unclosed
    assert got[0]["bottom"] == 100.0 and got[0]["closed"] is False and got[0]["age_weeks"] > 3
    assert ages == sorted(ages, reverse=True)                  # oldest first → largest age first


def test_nwog_empty_without_weekend_gap():
    t0 = datetime(2026, 6, 1, 14, 0, tzinfo=_UTC)
    w, _ = _week(t0, [100, 101, 102, 103, 104])               # contiguous, no >24h gap
    assert pdarrays.nwogs(w) == []


# ---- ORG (Lesson 14) --------------------------------------------------------------------------
_ET = timezone(timedelta(hours=-4))            # EDT (2026-06 is DST) — build bars at known ET times


def _bar15(et_dt, o, c):
    t = et_dt.astimezone(_UTC)
    return Bar("15m", t, t + timedelta(minutes=15), o, max(o, c) + 1, min(o, c) - 1, c, 100.0)


def test_org_prior_close_to_today_open_and_midpoint():
    """Lesson 14: ORG = prior day's 16:15 ET close → today's 09:30 ET open; key level = 50% midpoint;
    current day only. Example from the lesson: close 100, open 200 → ORG 50% = 150."""
    d1 = datetime(2026, 6, 1, tzinfo=_ET)                     # Monday
    d2 = datetime(2026, 6, 2, tzinfo=_ET)                     # Tuesday (today)
    bars = [
        _bar15(d1.replace(hour=16, minute=0), 100, 100),      # prior day ≤16:15 → close 100
        _bar15(d2.replace(hour=9, minute=30), 200, 205),      # today 09:30 open = 200 (gap up)
        _bar15(d2.replace(hour=9, minute=45), 205, 206),
    ]
    g = pdarrays.org(bars)
    assert g is not None
    assert g["bottom"] == 100.0 and g["top"] == 200.0 and g["mid"] == 150.0
    assert g["closed"] is False                               # price never closed back to 100


def test_org_closed_when_price_returns_to_prior_close():
    d1 = datetime(2026, 6, 1, tzinfo=_ET)
    d2 = datetime(2026, 6, 2, tzinfo=_ET)
    bars = [
        _bar15(d1.replace(hour=16, minute=0), 100, 100),
        _bar15(d2.replace(hour=9, minute=30), 200, 190),
        _bar15(d2.replace(hour=10, minute=0), 190, 99),       # closes 99 ≤ 100 → rebalanced
    ]
    assert pdarrays.org(bars)["closed"] is True


def test_org_none_before_open_or_without_prior_day():
    d2 = datetime(2026, 6, 2, tzinfo=_ET)
    # only today's bars, no prior day → cannot form
    assert pdarrays.org([_bar15(d2.replace(hour=9, minute=30), 200, 205)]) is None
    # prior day exists but no 09:30 bar today yet → None
    d1 = datetime(2026, 6, 1, tzinfo=_ET)
    pre = [_bar15(d1.replace(hour=16, minute=0), 100, 100),
           _bar15(d2.replace(hour=8, minute=0), 150, 150)]    # 08:00, before 09:30
    assert pdarrays.org(pre) is None
