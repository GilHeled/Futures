"""Standalone HRLR/LRLR liquidity-run model — faithful port of the Pine indicator. Deterministic,
read-only; no dependency on the trading engine."""
from ict_v2 import liquidity_runs as LR


def bar(h, l):                                    # minimal dict bar {'h','l','t'}
    return {"h": h, "l": l, "o": (h + l) / 2, "c": (h + l) / 2, "t": None}


def _series(highs, lows):
    return [bar(h, l) for h, l in zip(highs, lows)]


def test_pivot_high_needs_left_and_right():
    # left=2,right=1: index 3 (high 20) is strictly above the 2 left + 1 right neighbours
    highs = [10, 11, 12, 20, 13, 12, 11]
    lows = [h - 5 for h in highs]
    piv = LR._pivots(_series(highs, lows), left=2, right=1, high_side=True)
    assert (3, 20) in piv


def test_hrlr_sweep_then_lrlr_equal_then_standard():
    # three well-separated pivot highs: 20 (first→standard), 30 (higher→HRLR sweep),
    # 30.5 (within 2-tick*0.25 tol → LRLR equal high), 25 (lower→standard)
    highs = [10, 11, 20, 11, 10, 11, 30, 11, 10, 11, 30.5, 11, 10, 11, 25, 11, 10]
    lows = [h - 5 for h in highs]
    runs = LR.detect_liquidity_runs(_series(highs, lows), left=2, right=2, tol_ticks=2, tick=0.25)
    highs_runs = [r for r in runs if r.is_high]
    kinds = [(r.price, r.kind) for r in highs_runs]
    assert (20.0, "STANDARD") in kinds
    assert (30.0, "HRLR") in kinds
    assert (30.5, "LRLR") in kinds          # equal-high within tolerance beats "higher" → LRLR
    assert (25.0, "STANDARD") in kinds      # lower high than prev, beyond tolerance


def test_lrlr_precedence_over_hrlr():
    # a higher high but within tolerance must classify LRLR, not HRLR (Pine checks LRLR first)
    highs = [10, 11, 20.0, 11, 10, 11, 20.4, 11, 10]     # 20.0 → 20.4 = 0.4 = 1.6 ticks ≤ 2
    lows = [h - 5 for h in highs]
    runs = LR.detect_liquidity_runs(_series(highs, lows), left=2, right=2, tol_ticks=2, tick=0.25)
    second = [r for r in runs if r.is_high and abs(r.price - 20.4) < 1e-9][0]
    assert second.kind == "LRLR"


def test_low_side_sweep_is_bullish():
    lows = [50, 49, 40, 49, 50, 49, 30, 49, 50]          # 40 (standard), 30 (lower low → HRLR bull)
    highs = [l + 5 for l in lows]
    runs = LR.detect_liquidity_runs(_series(highs, lows), left=2, right=2, tol_ticks=2, tick=0.25)
    swept = [r for r in runs if not r.is_high and abs(r.price - 30) < 1e-9][0]
    assert swept.kind == "HRLR" and swept.bias == "bullish" and swept.side == "SSL"


def test_mitigation_high_pool():
    # a pivot high at 20, later a bar trades to 21 → mitigated at that bar
    highs = [10, 11, 20, 11, 10, 21, 12]
    lows = [h - 5 for h in highs]
    runs = LR.detect_liquidity_runs(_series(highs, lows), left=2, right=2, tol_ticks=1, tick=0.25)
    ph = [r for r in runs if r.is_high and abs(r.price - 20) < 1e-9][0]
    assert ph.mitigated and ph.mitigation_index == 5


def test_unmitigated_high_pool_stays_open():
    highs = [10, 11, 20, 11, 10, 12, 11]                 # nothing later reaches 20
    lows = [h - 5 for h in highs]
    runs = LR.detect_liquidity_runs(_series(highs, lows), left=2, right=2, tol_ticks=1, tick=0.25)
    ph = [r for r in runs if r.is_high and abs(r.price - 20) < 1e-9][0]
    assert not ph.mitigated and ph.mitigation_index is None


def test_summary_shape_and_readonly():
    highs = [10, 11, 20, 11, 10, 11, 30, 11, 10, 11, 30.4, 11, 10]
    lows = [h - 5 for h in highs]
    runs = LR.detect_liquidity_runs(_series(highs, lows), left=2, right=2, tol_ticks=2, tick=0.25)
    s = LR.summary(runs)
    assert set(s) >= {"total", "lrlr", "hrlr", "standard", "unmitigated", "pools"}
    assert s["total"] == len(runs) and s["lrlr"] >= 1 and s["hrlr"] >= 1
