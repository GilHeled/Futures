"""Displacement detector (B5): start = manip extreme, end = first width-1 counter-pivot; causal."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure.displacement import detect_displacements
from ict_live.structure.manipulation import Sweep

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _bar(i, o, h, l, c):
    return Bar("1H", T0 + timedelta(hours=i), T0 + timedelta(hours=i + 1), o, h, l, c, 1.0)


def _sweep(direction, extreme, bar_index):
    return Sweep(id=f"SWP@{bar_index}", direction=direction, pool_price=extreme,
                 extreme=extreme, bar_index=bar_index, time=T0 + timedelta(hours=bar_index),
                 pool_index=0, close=extreme, reason="x", depends_on=("ERL0",))


def test_bearish_displacement_to_first_counter_pivot():
    # manip high at bar 1 (110), then a down impulse making a minor low at bar 3, then up
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 105, 110, 104, 106), _bar(2, 104, 105, 96, 98),
            _bar(3, 98, 99, 90, 95), _bar(4, 95, 102, 94, 101), _bar(5, 101, 103, 100, 102)]
    disp = detect_displacements([_sweep("bearish", 110.0, 1)], bars)
    assert len(disp) == 1
    d = disp[0]
    assert d.direction == "bearish" and d.start_index == 1 and d.start_price == 110
    assert d.end_index == 3 and d.end_price == 90 and d.net == 20 and d.exhausted is True
    assert d.depends_on == ("SWP@1",)


def test_bullish_displacement():
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 96, 97, 90, 95), _bar(2, 95, 106, 94, 104),
            _bar(3, 104, 112, 103, 110), _bar(4, 110, 111, 100, 101), _bar(5, 101, 102, 99, 100)]
    disp = detect_displacements([_sweep("bullish", 90.0, 1)], bars)
    assert disp[0].direction == "bullish" and disp[0].start_price == 90
    assert disp[0].end_index == 3 and disp[0].end_price == 112 and disp[0].net == 22


def test_in_progress_when_no_counter_pivot():
    # steady down move with no confirmed minor low before the end -> not exhausted, ends at last bar
    bars = [_bar(0, 110, 112, 108, 109), _bar(1, 109, 110, 100, 101), _bar(2, 101, 102, 92, 93),
            _bar(3, 93, 94, 85, 86)]
    disp = detect_displacements([_sweep("bearish", 112.0, 0)], bars)
    assert disp and disp[0].exhausted is False and disp[0].end_index == len(bars) - 1


def test_no_displacement_if_no_move_in_direction():
    # after a bearish sweep price goes UP -> no bearish displacement
    bars = [_bar(0, 100, 110, 99, 105), _bar(1, 105, 120, 104, 118), _bar(2, 118, 121, 112, 115)]
    assert detect_displacements([_sweep("bearish", 110.0, 0)], bars) == []
