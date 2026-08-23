"""Execution-quality MVP layer: factors in [0,1], TRADE/PASS output, N/A when no live setup,
and it never alters the structural recommendation."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import execution_quality as EQ
from ict_live.engine import pipeline
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _series(n, seed):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(minutes=15 * i)
        bars.append(Bar("15m", ot, ot + timedelta(minutes=15), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_factors_and_assessment_on_live_setup():
    for seed in range(1, 120):
        ms = pipeline.analyze(_series(240, seed=seed), "15m")
        if ms.recommendation.decision in ("LONG", "SHORT"):
            f = EQ.factors(ms)
            assert set(f) == set(EQ.FACTOR_NAMES)
            assert all(0.0 <= v <= 1.0 for v in f.values())
            a = EQ.assess(ms)
            assert a.structural == ms.recommendation.decision       # structural unchanged
            assert a.execution in ("TRADE", "PASS")
            assert 0.0 <= a.confidence <= 1.0 and a.reason
            # v1 score is the transparent 0.6·pd + 0.4·ce weighted mean
            expect_q = round(0.6 * f["pd_location"] + 0.4 * f["ce_distance"], 4)
            assert a.confidence == expect_q
            assert a.execution == ("TRADE" if expect_q >= EQ.V1_THRESHOLD else "PASS")
            # explainability draws only from SCORED factors (pd_location, ce_distance), worst-first
            scored = {"pd_location", "ce_distance"}
            assert a.weakest_factor in scored
            flagged = [k for k in ("pd_location", "ce_distance") if f[k] < EQ._ISSUE_BAR]
            assert len(a.reasons) == len(flagged)
            vals = [next(f[k] for k in scored if f"{k}=" in r) for r in a.reasons]
            assert vals == sorted(vals) and all(v < EQ._ISSUE_BAR for v in vals)
            return
    raise AssertionError("no live setup found")


def test_v1_is_frozen():
    """Execution v1 is FROZEN (2026-08-22) after passing the Batch-3 validation gate
    (structural 67/67; execution vs would_execute: balacc 1.0 / false-PASS 0.0 / PASS-recall 1.0).
    These constants must not change without a new pre-registered validation."""
    assert EQ.V1_WEIGHTS == {"pd_location": 0.6, "ce_distance": 0.4,
                             "rr_realism": 0.0, "confirmation": 0.0, "fvg_location": 0.0}
    assert EQ.V1_THRESHOLD == 0.39


def test_exit_model_is_frozen():
    """Execution EXIT model FROZEN after locked-OOS confirmation (2026-08-22): full exit at +2R.
    Must not change without a new pre-registered OOS test."""
    assert EQ.EXIT_MODEL == "fixed_2R"
    assert EQ.EXIT_TARGET_R == 2.0


def test_no_setup_is_na():
    t0 = datetime(2026, 6, 1, 18, tzinfo=ET)
    flat = [Bar("15m", t0 + timedelta(minutes=15 * i), t0 + timedelta(minutes=15 * (i + 1)),
                100, 100.5, 99.5, 100, 1.0) for i in range(80)]
    ms = pipeline.analyze(flat, "15m")
    a = EQ.assess(ms)
    assert a.structural == "NO-TRADE" and a.execution == "N/A" and a.factors == {}


def test_threshold_controls_trade_pass():
    for seed in range(1, 120):
        ms = pipeline.analyze(_series(240, seed=seed), "15m")
        if ms.recommendation.decision in ("LONG", "SHORT"):
            assert EQ.assess(ms, threshold=0.0).execution == "TRADE"    # everything passes
            assert EQ.assess(ms, threshold=1.01).execution == "PASS"    # nothing passes
            return
