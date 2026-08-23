"""Engine-vs-human fidelity comparison: agreement, and classification of each disagreement type."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import pipeline
from ict_live.research.fidelity_compare import CATEGORIES, SceneLabel, compare

ET = ZoneInfo("America/New_York")


def _series(n, seed):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(minutes=15 * i)
        bars.append(pipeline.Bar("15m", ot, ot + timedelta(minutes=15), o, max(o, c) + (x % 7),
                                 min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


# NOTE: Bar is re-exported for the test via pipeline module namespace
from ict_live.market.bar import Bar  # noqa: E402
pipeline.Bar = Bar


def _ms_with_sweeps():
    for seed in range(1, 60):
        ms = pipeline.analyze(_series(200, seed), "15m")
        if len(ms.ranked_sweeps) >= 2:
            return ms
    raise AssertionError("no scene with >=2 sweeps found")


def test_direction_agreement_and_context_disagreement():
    ms = _ms_with_sweeps()
    eng = ms.recommendation.decision
    lab_agree = SceneLabel("s1", "MNQ", "2026-06-01", "15m", human_decision=eng)
    assert compare(ms, lab_agree)["dimensions"][0]["agree"] is True
    # opposite direction -> context (only meaningful if engine actually traded)
    if eng in ("LONG", "SHORT"):
        opp = "SHORT" if eng == "LONG" else "LONG"
        r = compare(ms, SceneLabel("s2", "MNQ", "d", "15m", human_decision=opp))
        d0 = r["dimensions"][0]
        assert d0["agree"] is False and d0["category"] == "context"


def test_manipulation_ranking_signal_and_categories():
    ms = _ms_with_sweeps()
    top = ms.ranked_sweeps[0].item
    second = ms.ranked_sweeps[1].item
    # human picks engine's #1 -> agree, rank 1
    r1 = compare(ms, SceneLabel("m1", "MNQ", "d", "15m",
                                human_manip_direction=top.direction, human_manip_level=top.pool_price))
    md = next(d for d in r1["dimensions"] if d["dimension"] == "manipulation")
    assert md["agree"] is True and md["human_chosen_engine_rank"] == 1
    # human picks engine's #2 -> ranking problem, rank 2 (the fidelity-ranking signal)
    r2 = compare(ms, SceneLabel("m2", "MNQ", "d", "15m",
                                human_manip_direction=second.direction, human_manip_level=second.pool_price))
    md2 = next(d for d in r2["dimensions"] if d["dimension"] == "manipulation")
    assert md2["human_chosen_engine_rank"] == 2 and md2["category"] in ("ranking", "context")
    # human marks a level the engine never produced -> detector problem
    r3 = compare(ms, SceneLabel("m3", "MNQ", "d", "15m",
                                human_manip_direction=top.direction, human_manip_level=1.0))
    md3 = next(d for d in r3["dimensions"] if d["dimension"] == "manipulation")
    assert md3["human_chosen_engine_rank"] is None and md3["category"] == "detector"


def test_record_shape_and_categories_valid():
    ms = _ms_with_sweeps()
    r = compare(ms, SceneLabel("z", "MNQ", "d", "15m", human_decision="NO_TRADE",
                               human_manip_level=1.0, human_manip_direction="bearish", confidence=0.7,
                               note="test"))
    assert r["type"] == "fidelity_comparison" and r["scene_id"] == "z"
    for d in r["dimensions"]:
        if d.get("category"):
            assert d["category"] in CATEGORIES
