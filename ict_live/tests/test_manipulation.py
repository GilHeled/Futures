"""Manipulation (liquidity sweep) detector: raid+rejection vs acceptance, direction, deps."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure import ids
from ict_live.structure.manipulation import detect_sweeps
from ict_live.structure.swing_liquidity import SwingPool

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _bar(i, o, h, l, c):
    return Bar("1H", T0 + timedelta(hours=i), T0 + timedelta(hours=i + 1), o, h, l, c, 1.0)


def _pool(kind, price, index, swept_index):
    return SwingPool(kind, price, T0 + timedelta(hours=index), index,
                     swept=swept_index is not None, swept_index=swept_index, reason="x")


def test_bearish_manipulation_buyside_raid_and_rejection():
    # bar 3 wicks above 100 (to 105) then closes back below (98) -> bearish manipulation
    bars = [_bar(0, 90, 95, 88, 94), _bar(1, 94, 101, 93, 100), _bar(2, 100, 100, 96, 98),
            _bar(3, 98, 105, 97, 98)]
    sweeps = detect_sweeps([_pool("high", 100.0, 1, 3)], bars)
    assert len(sweeps) == 1
    s = sweeps[0]
    assert s.direction == "bearish" and s.extreme == 105 and s.bar_index == 3
    assert ids.pool_id(_pool("high", 100.0, 1, 3)) in s.depends_on
    assert ids.bar_id(3) in s.depends_on


def test_bullish_manipulation_sellside_raid_and_rejection():
    bars = [_bar(0, 100, 101, 95, 96), _bar(1, 96, 97, 90, 92), _bar(2, 92, 94, 88, 93)]
    sweeps = detect_sweeps([_pool("low", 90.0, 1, 2)], bars)   # bar2 dips to 88 then closes 93>90
    assert len(sweeps) == 1 and sweeps[0].direction == "bullish" and sweeps[0].extreme == 88


def test_acceptance_close_beyond_is_not_manipulation():
    # bar 3 wicks above 100 AND closes above (103) -> acceptance / BOS, not manipulation
    bars = [_bar(0, 90, 95, 88, 94), _bar(1, 94, 101, 93, 100), _bar(2, 100, 100, 96, 98),
            _bar(3, 98, 105, 97, 103)]
    assert detect_sweeps([_pool("high", 100.0, 1, 3)], bars) == []


def test_unswept_pool_yields_no_sweep():
    bars = [_bar(0, 90, 95, 88, 94), _bar(1, 94, 101, 93, 100)]
    assert detect_sweeps([_pool("high", 100.0, 1, None)], bars) == []
