"""Fidelity shadow ranker: schema, engine-only features, and honest insufficient-data abstention."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import pipeline
from ict_live.market.bar import Bar
from ict_live.research import fidelity_ranking as FR

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


def _example(seed):
    bars = _series(200, seed)
    ms = pipeline.analyze(bars, "15m")
    return FR.example_from_state(ms, bars[-1], scene_id=f"s{seed}", symbol="MNQ",
                                 date="2026-06-01", tf="15m",
                                 human_decision="NO_TRADE", provenance="test"), ms, bars[-1]


def test_example_carries_engine_only_features():
    ex, ms, bar = _example(3)
    assert ex.scene_id == "s3" and ex.tf == "15m"
    for c in ex.candidates:
        keys = set(c["features"])
        # every allowed feature key is present; no outcome/label key leaks in
        assert set(FR.FEATURE_KEYS + FR.CAT_KEYS) <= keys
        assert not ({"outcome", "labels", "realized_R", "target_before_stop"} & keys)


def test_ranker_reports_insufficient_and_abstains():
    examples = [_example(s)[0] for s in range(1, 7)]     # only 6 scenes, no candidate-level choices
    r = FR.FidelityRanker().fit(examples)
    assert r.trained is False and "insufficient_training_data" in r.status
    ex, ms, bar = _example(9)
    rep = FR.shadow_report(ms, r, bar)
    assert rep["shadow_only"] is True
    assert rep["fidelity_shadow"]["abstain"] is True
    assert all(pc["p"] is None for pc in rep["fidelity_shadow"]["per_candidate"])
    assert rep["fidelity_shadow"]["p_no_trade"] is None
    # deterministic recommendation is reported unchanged (shadow never overrides it)
    assert rep["deterministic"] == ms.recommendation.decision
