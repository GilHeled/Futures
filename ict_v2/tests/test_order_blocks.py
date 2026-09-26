"""Standalone Order Blocks (ICT) model — faithful Pine port. Deterministic; no engine dependency."""
from ict_v2 import order_blocks as OB


def b(o, h, l, c):
    return {"o": o, "h": h, "l": l, "c": c, "t": None}


# A hand-verified bullish-OB scenario (swing_len=2): pivot high 25 → swing low (down-close) at bar 5 →
# rally to a higher high (26) confirmed at bar 9. OB candle = bar 5: top=high(15), bottom=low(10).
BULL = [b(19, 20, 18, 19), b(21, 22, 20, 21), b(24, 25, 23, 24), b(20, 21, 19, 20), b(20, 20, 18, 19),
        b(14, 15, 10, 12), b(12, 21, 12, 20), b(20, 26, 20, 22), b(22, 24, 21, 23), b(21, 22, 20, 21)]


def _reflect(bars, K=40):                    # mirror OHLC so a bull scenario becomes a bear one
    return [b(K - x["o"], K - x["l"], K - x["h"], K - x["c"]) for x in bars]


def test_no_blocks_on_flat_series():
    assert OB.detect_order_blocks([b(10, 10.5, 9.5, 10) for _ in range(20)], swing_len=3) == []


def test_bullish_ob_on_higher_high():
    obs = OB.detect_order_blocks(BULL, swing_len=2, max_bars=100)
    assert len(obs) == 1
    ob = obs[0]
    assert ob.is_bull and ob.label == "OB+"
    assert ob.top == 15.0 and ob.bottom == 10.0 and ob.mid == 12.5
    assert ob.left_index == 5 and ob.state == "active"


def test_bearish_ob_on_lower_low():
    obs = OB.detect_order_blocks(_reflect(BULL), swing_len=2, max_bars=100)
    assert len(obs) == 1
    ob = obs[0]
    assert (not ob.is_bull) and ob.label == "OB-"
    assert ob.top == 30.0 and ob.bottom == 25.0 and ob.state == "active"


def test_mitigation_marks_block():
    # append a bar that closes (6) below the bull OB bottom (10) -> mitigated at that bar
    obs = OB.detect_order_blocks(BULL + [b(11, 11, 5, 6)], swing_len=2, max_bars=100)
    assert len(obs) == 1 and obs[0].state == "mitigated" and obs[0].mitigation_index == 10


def test_summary_visible_limits_and_shape():
    s = OB.summary(OB.detect_order_blocks(BULL, swing_len=2), max_bull=1, max_bear=1)
    assert set(s) >= {"visible", "active_bull", "active_bear", "total"}
    assert s["active_bull"] == 1 and s["total"] == 1
    assert len([o for o in s["visible"] if o["is_bull"]]) == 1
    for o in s["visible"]:
        assert o["state"] == "active" and o["label"] in ("OB+", "OB-") and o["mid"] == (o["top"] + o["bottom"]) / 2
