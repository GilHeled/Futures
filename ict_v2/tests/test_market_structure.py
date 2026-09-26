"""Standalone Market Structure (BOS/MSS) model — faithful Pine port. Deterministic; no engine dep."""
from ict_v2 import market_structure as MS


def b(o, h, l, c):
    return {"o": o, "h": h, "l": l, "c": c, "t": None}


# swing high 15 (bar 2) broken at bar 5 -> first break = bull MSS; new swing high 20 (bar 7) broken at
# bar 10 while already bull -> bull BOS (continuation).
BULL = [b(10, 12, 9, 11), b(11, 13, 10, 12), b(12, 15, 11, 13), b(13, 14, 12, 13), b(13, 14, 12, 12),
        b(12, 16, 12, 16), b(16, 17, 15, 16), b(16, 20, 15, 17), b(17, 18, 16, 17), b(17, 18, 16, 17),
        b(17, 21, 17, 21)]


def _reflect(bars, K=40):
    return [b(K - x["o"], K - x["l"], K - x["h"], K - x["c"]) for x in bars]


def test_no_events_on_flat_series():
    assert MS.detect_market_structure([b(10, 10.5, 9.5, 10) for _ in range(20)], pivot_strength=3) == []


def test_bull_mss_then_bos():
    ev = MS.detect_market_structure(BULL, pivot_strength=2)
    assert [(e.kind, e.direction, e.level) for e in ev] == [
        ("MSS", "bull", 15.0), ("BOS", "bull", 20.0)]
    assert ev[0].from_index == 2 and ev[0].break_index == 5     # line from swing -> break bar


def test_bear_mss_then_bos_via_reflection():
    ev = MS.detect_market_structure(_reflect(BULL), pivot_strength=2)
    assert [(e.kind, e.direction, e.level) for e in ev] == [
        ("MSS", "bear", 25.0), ("BOS", "bear", 20.0)]


def test_first_break_is_always_mss():
    # a single break with no prior direction must be MSS, never BOS
    ev = MS.detect_market_structure(BULL[:6], pivot_strength=2)
    assert len(ev) == 1 and ev[0].kind == "MSS"


def test_detect_pivots():
    piv = MS.detect_pivots(BULL, pivot_strength=2)
    assert piv and all(p["kind"] in ("high", "low") for p in piv)
    highs = [p for p in piv if p["kind"] == "high"]
    assert any(p["price"] == 15.0 for p in highs)        # the swing high at bar 2
    # every pivot has enough bars on each side to be confirmed
    assert all(2 <= p["index"] <= len(BULL) - 1 - 2 for p in piv)


def test_summary_shape_and_counts():
    s = MS.summary(MS.detect_market_structure(BULL, pivot_strength=2))
    assert set(s) >= {"events", "bull_mss", "bull_bos", "bear_mss", "bear_bos", "total"}
    assert s["bull_mss"] == 1 and s["bull_bos"] == 1 and s["total"] == 2
    for e in s["events"]:
        assert e["kind"] in ("BOS", "MSS") and e["direction"] in ("bull", "bear")
