"""Annotation app: session ordering/resume bookkeeping + keyboard panel content (no server/pandas)."""
from ict_live.research.annotation_app import Session, _panel


def _q(*ids):
    return [{"scene_id": s, "symbol": "MES", "contract": "MESH22", "signal_tf": "1H",
             "time": f"2022-01-0{i}T10:00:00-05:00", "score": 10 - i} for i, s in enumerate(ids, 1)]


def test_session_ordering_and_resume(tmp_path):
    q = _q("a", "b", "c")
    s = Session(str(tmp_path / "sess.json"), q)
    assert s.next_index() == 0                       # queue is pre-sorted by info value
    s.reviewed["a"] = {"decisions": ["ACCEPT", "LONG"]}
    assert s.next_index() == 1
    s.deferred.append("b")
    assert s.next_index() == 2                        # skips reviewed(a) + deferred(b)
    s.reviewed["c"] = {"decisions": ["NO_TRADE"]}
    assert s.next_index() == 1                        # only deferred b left -> fallback to it
    s.save()
    # resume from disk
    s2 = Session(str(tmp_path / "sess.json"), q)
    assert s2.reviewed.keys() == {"a", "c"} and s2.deferred == ["b"]


def test_progress_counts(tmp_path):
    s = Session(str(tmp_path / "s.json"), _q("a", "b", "c", "d"))
    s.reviewed["a"] = {}; s.deferred.append("b")
    p = s.progress()
    assert p == {"reviewed": 1, "deferred": 1, "total": 4}


def test_panel_has_keyboard_controls():
    scene = _q("x")[0]
    html = _panel(scene, 0, prior=None, prog={"reviewed": 0, "deferred": 0, "total": 5},
                  version_changed=True)
    for token in (">L<", ">S<", ">N<", ">A<", ">R<", "save + next", "wrong_manipulation",
                  "insufficient_confirmation", "changed vs prior engine", "info"):
        assert token in html
    # confidence 1..5 hotkeys present
    for d in "12345":
        assert f">{d}<" in html


def test_panel_shows_prior_annotation():
    scene = _q("x")[0]
    prior = {"decisions": ["REJECT", "NO_TRADE"], "confidence": 0.6,
             "error_tags": ["wrong_manipulation"], "annotator": "gil"}
    html = _panel(scene, 0, prior=prior, prog={"reviewed": 1, "deferred": 0, "total": 3},
                  version_changed=False)
    assert "prior label" in html and "REJECT/NO_TRADE" in html and "wrong_manipulation" in html
