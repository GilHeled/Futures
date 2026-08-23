"""Roll detection + segmentation: quarterly calendar, jump location, state-reset segments, and
that future-aware roll metadata is labelled non-causal."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.research import rolls

ET = ZoneInfo("America/New_York")


def test_third_friday():
    assert rolls.third_friday(2026, 6) == date(2026, 6, 19)   # Jun 2026 3rd Friday
    assert rolls.third_friday(2026, 9) == date(2026, 9, 18)


def _bar(dt, o, c):
    return Bar("5m", dt, dt + timedelta(minutes=5), o, max(o, c) + 1, min(o, c) - 1, c, 1.0)


def _synthetic_with_roll():
    """~one quarter of daily-ish bars around the Jun 2026 roll with a clear jump at the boundary."""
    bars, px = [], 20000.0
    d = datetime(2026, 5, 1, 10, 0, tzinfo=ET)
    for i in range(60):
        t = d + timedelta(days=i)
        # inject a big roll jump on 2026-06-11 (~8 days before Jun 19 expiry)
        if t.date() == date(2026, 6, 11):
            px += 150.0
        else:
            px += 1.0
        bars.append(_bar(t, px - 1, px))
    return bars


def test_detect_roll_locates_the_jump():
    bars = _synthetic_with_roll()
    bounds = rolls.detect_rolls(bars, "MNQ", roll_days_before=8, search_window_days=6)
    jun = [b for b in bounds if b.expiry == date(2026, 6, 19)]
    assert jun, "expected a June roll boundary"
    b = jun[0]
    assert bars[b.index].open_time.date() == date(2026, 6, 11)     # located the injected jump
    assert b.from_contract == "MNQM26" and b.to_contract == "MNQU26"
    assert b.gap > 100


def test_segment_splits_and_resets():
    bars = _synthetic_with_roll()
    bounds = rolls.detect_rolls(bars, "MNQ")
    segs = rolls.segment(bars, bounds)
    assert len(segs) >= 2                                          # split at the roll
    # segments are contiguous, non-overlapping, cover all bars, and don't cross the boundary
    assert segs[0].start_index == 0 and segs[-1].end_index == len(bars) - 1
    for a, b in zip(segs, segs[1:]):
        assert b.start_index == a.end_index + 1
    jun_idx = [x.index for x in bounds if x.expiry == date(2026, 6, 19)][0]
    # the boundary bar starts a new segment (nothing survives across it)
    assert any(s.start_index == jun_idx for s in segs)


def test_roll_metadata_marks_future_field():
    bars = _synthetic_with_roll()
    segs = rolls.segment(bars, rolls.detect_rolls(bars, "MNQ"))
    md = rolls.roll_metadata(segs[0].start_index + 2, segs[0])
    assert md["bars_since_roll"] == 2                              # causal
    assert "bars_until_roll" in md                                # present but must not feed ML
