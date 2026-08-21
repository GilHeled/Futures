"""Dealing-range DETECTOR (experimental): per-TF discovery, CE, zones, source tagging, rationale.
The detector never decides which range is 'the' active one — that's the context layer's job."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.structure.dealing_range import dealing_ranges, range_for_tf
from ict_live.structure.swings import Swing

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _sw(kind, idx, price):
    return Swing(kind, idx, idx + 2, T0 + timedelta(hours=idx), price)


def test_range_for_tf_up_leg_and_zones():
    structural = [_sw("high", 1, 100), _sw("low", 3, 80), _sw("high", 5, 120), _sw("low", 4, 90)]
    dr = range_for_tf(structural, "1H")
    assert dr.source_tf == "1H" and dr.high == 120 and dr.low == 90 and dr.ce == 105
    assert dr.direction == "up"
    assert dr.zone_of(110) == "premium" and dr.zone_of(100) == "discount"
    assert dr.zone_of(105) == "equilibrium"
    assert "[1H]" in dr.reason and "CE(50%)=105" in dr.reason


def test_down_leg_direction():
    dr = range_for_tf([_sw("low", 2, 50), _sw("high", 4, 70), _sw("low", 6, 55)], "15m")
    assert dr.direction == "down" and dr.high == 70 and dr.low == 55 and dr.source_tf == "15m"


def test_none_without_both_sides_or_degenerate():
    assert range_for_tf([_sw("high", 1, 100), _sw("high", 3, 120)], "1H") is None
    assert range_for_tf([], "1H") is None
    assert range_for_tf([_sw("high", 1, 80), _sw("low", 3, 100)], "1H") is None   # hi<=lo guard


def test_dealing_ranges_hierarchy_one_per_tf():
    by_tf = {
        "1H": [_sw("low", 1, 90), _sw("high", 3, 110)],
        "D": [_sw("low", 1, 50), _sw("high", 5, 150)],
        "4H": [_sw("high", 2, 100)],           # incomplete -> no range for 4H
    }
    ranges = dealing_ranges(by_tf)
    tfs = {dr.source_tf for dr in ranges}
    assert tfs == {"1H", "D"}                  # 4H dropped (only a high); no single winner chosen
    d = next(dr for dr in ranges if dr.source_tf == "D")
    assert d.low == 50 and d.high == 150 and d.ce == 100
