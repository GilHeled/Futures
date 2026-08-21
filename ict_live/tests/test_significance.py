"""Objective structural swing classifier (experimental): skeleton reduction + `dominant`
diagnostic. 'significant' is intentionally NOT a tier — liquidity relevance lives elsewhere."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure.significance import classify, counts, structural_swings
from ict_live.structure.swings import Swing

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _bars(closes):
    out = []
    for i, c in enumerate(closes):
        ot = T0 + timedelta(hours=i)
        out.append(Bar("1H", ot, ot + timedelta(hours=1), c, c + 100, c - 100, c, 1.0))
    return out


def _sw(kind, idx, price):
    return Swing(kind, idx, idx + 2, T0 + timedelta(hours=idx), price)


def test_consecutive_same_side_collapse_to_extreme():
    cands = [_sw("high", 1, 10), _sw("high", 3, 12), _sw("low", 5, 5)]
    cls = {(c.swing.kind, c.swing.index): c for c in classify(cands, _bars([8] * 8))}
    assert cls[("high", 1)].tier == "rejected"          # less-extreme same-side pivot dropped
    assert cls[("high", 3)].tier == "structural"
    assert cls[("low", 5)].tier == "structural"


def test_dominant_is_diagnostic_not_a_tier():
    # rising highs 10 -> 12: both structural, only the higher one is `dominant`.
    cands = [_sw("high", 1, 10), _sw("low", 3, 5), _sw("high", 5, 12)]
    cls = {(c.swing.kind, c.swing.index): c for c in classify(cands, _bars([8] * 8))}
    assert cls[("high", 1)].tier == "structural" and cls[("high", 1)].dominant is False
    assert cls[("high", 5)].tier == "structural" and cls[("high", 5)].dominant is True


def test_monotone_trend_has_one_dominant_each_side():
    cands = []
    for i, (hi, lo) in enumerate([(10, 4), (12, 6), (14, 8), (16, 10)]):
        cands.append(_sw("high", 2 * i + 1, hi))
        cands.append(_sw("low", 2 * i + 2, lo))
    out = classify(cands, _bars([0] * 40))
    dom_hi = [c for c in out if c.swing.kind == "high" and c.dominant]
    dom_lo = [c for c in out if c.swing.kind == "low" and c.dominant]
    assert len(dom_hi) == 1 and dom_hi[0].swing.price == 16     # capping high
    assert len(dom_lo) == 1 and dom_lo[0].swing.price == 4      # origin low
    assert all(c.tier == "structural" for c in out)            # all alternating -> all structural


def test_counts_and_structural_helper():
    cands = [_sw("high", 1, 10), _sw("high", 3, 12), _sw("low", 5, 5)]
    out = classify(cands, _bars([8] * 8))
    c = counts(out)
    assert c["rejected"] == 1 and c["structural"] == 2 and sum(c.values()) == 3
    assert len(structural_swings(out)) == 2


def test_broken_flag_is_causal():
    cands = [_sw("high", 5, 12)]
    out = classify(cands, _bars([99, 99, 99, 99, 99, 8, 8]))   # big closes precede idx5
    assert out[0].broken is False and out[0].tier == "structural"
