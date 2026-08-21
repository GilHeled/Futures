"""Every derived engine object must carry a human-readable WHY (visual-audit requirement)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure.dealing_range import range_for_tf
from ict_live.structure.significance import classify
from ict_live.structure.swing_liquidity import swing_liquidity
from ict_live.structure.swings import Swing

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _bars(n):
    return [Bar("1H", T0 + timedelta(hours=i), T0 + timedelta(hours=i + 1),
                100, 200, 10, 100, 1.0) for i in range(n)]


def _sw(kind, idx, price):
    return Swing(kind, idx, idx + 2, T0 + timedelta(hours=idx), price)


def test_all_derived_objects_have_reasons():
    cands = [_sw("high", 1, 150), _sw("high", 3, 160), _sw("low", 5, 40), _sw("high", 7, 170)]
    bars = _bars(10)
    classified = classify(cands, bars)
    assert all(cs.reason for cs in classified)                     # swings explain themselves
    structural = [cs.swing for cs in classified if cs.tier == "structural"]
    pools = swing_liquidity(structural, bars)
    assert all(p.reason for p in pools)                            # ERL pools explain themselves
    dr = range_for_tf(structural, "1H")
    assert dr is not None and dr.reason                            # dealing range explains itself
    # rejected swings say why they were dropped
    assert any("collapsed" in cs.reason for cs in classified if cs.tier == "rejected")
