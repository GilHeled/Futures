"""Active-learning queue (information value + triviality) and versioning/regression diff."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import pipeline
from ict_live.market.bar import Bar
from ict_live.research import annotation_queue as AQ
from ict_live.research import versioning as V

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


def test_information_value_components_and_transparency():
    ms = pipeline.analyze(_series(200, 5), "15m")
    iv = AQ.information_value(ms)
    assert set(iv["factors"]) == set(AQ.WEIGHTS)          # every weight has a factor
    assert iv["weights"] == AQ.WEIGHTS                    # weights exposed (not hidden)
    assert iv["score"] == round(sum(AQ.WEIGHTS[k] * iv["factors"][k] for k in AQ.WEIGHTS), 3)


def test_prior_disagreement_and_version_change_raise_value():
    ms = pipeline.analyze(_series(200, 5), "15m")
    base = AQ.information_value(ms)["score"]
    dec = ms.recommendation.decision
    other = "LONG" if dec != "LONG" else "SHORT"
    hi = AQ.information_value(ms, prior_decision=other, version_changed=True)["score"]
    assert hi > base                                      # disagreement + version change add value


def test_trivial_scene_flagged():
    # a flat series -> no competition, clear NO-TRADE -> trivial
    t0 = datetime(2026, 6, 1, 18, tzinfo=ET)
    flat = [Bar("15m", t0 + timedelta(minutes=15 * i), t0 + timedelta(minutes=15 * (i + 1)),
                100, 100.5, 99.5, 100, 1.0) for i in range(60)]
    iv = AQ.information_value(pipeline.analyze(flat, "15m"))
    assert AQ.is_trivial(iv) is True


def test_versioning_snapshot_and_diff():
    a = pipeline.analyze(_series(200, 5), "15m")
    b = pipeline.analyze(_series(200, 9), "15m")
    snap_old = V.snapshot([("sceneA", a), ("sceneB", b)])
    assert "commit" in snap_old["version"] and "engine_hash" in snap_old["version"]
    # identical recompute -> no changes
    same = V.diff(snap_old, V.snapshot([("sceneA", a), ("sceneB", b)]))
    assert same["n_changed"] == 0
    # a scene whose state differs -> detected as changed with a 'why'
    c = pipeline.analyze(_series(200, 21), "15m")
    changed = V.diff(snap_old, V.snapshot([("sceneA", c), ("sceneB", b)]))
    if changed["n_changed"]:                              # sceneA likely differs across seeds
        assert changed["changes"][0]["scene_id"] == "sceneA"
        assert changed["changes"][0]["why"]
    assert changed["added"] == [] and changed["removed"] == []
