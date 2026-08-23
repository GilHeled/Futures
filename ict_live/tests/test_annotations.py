"""Human-fidelity annotation schema: validation, error tags, separation from outcomes, round-trip."""
import pytest

from ict_live.engine import annotations as A
from ict_live.engine.outcomes import OUTCOME_TOP_KEYS


def test_valid_annotation_round_trip(tmp_path):
    a = A.make_annotation(candidate_id="SETUP:FVG22D", decisions=["REJECT", "NO_TRADE"],
                          annotator="gil", error_tags=["wrong_manipulation", "bad_location"],
                          note="raided minor liquidity, not the true draw", confidence=0.8,
                          candidate_time="2026-08-18T10:00:00-04:00")
    assert a["type"] == "human_fidelity" and a["candidate_id"] == "SETUP:FVG22D"
    p = tmp_path / "fidelity.jsonl"
    A.append_annotation(str(p), a)
    A.append_annotation(str(p), A.make_annotation(candidate_id="SETUP:FVG22D",
                        decisions=["ACCEPT", "SHORT"], annotator="gil", confidence=0.6))
    assert len(A.load_annotations(str(p))) == 2
    # latest-wins per candidate
    assert A.latest_by_candidate(str(p))["SETUP:FVG22D"]["decisions"] == ["ACCEPT", "SHORT"]


def test_all_error_tags_accepted():
    a = A.make_annotation(candidate_id="c", decisions=["REJECT"], annotator="g",
                          error_tags=sorted(A.ERROR_TAGS), note="covers other")
    assert set(a["error_tags"]) == A.ERROR_TAGS


@pytest.mark.parametrize("kwargs", [
    {"decisions": []},                                   # empty
    {"decisions": ["MAYBE"]},                            # unknown decision
    {"decisions": ["LONG"], "error_tags": ["nope"]},     # unknown tag
    {"decisions": ["LONG"], "confidence": 1.5},          # out of range
    {"decisions": ["REJECT"], "error_tags": ["other"]},  # 'other' without note
])
def test_invalid_annotations_rejected(kwargs):
    with pytest.raises(ValueError):
        A.make_annotation(candidate_id="c", annotator="g", **kwargs)


def test_requires_annotator():
    with pytest.raises(ValueError):
        A.make_annotation(candidate_id="c", decisions=["LONG"], annotator="  ")


def test_execution_fields():
    a = A.make_annotation(candidate_id="c", decisions=["REJECT", "NO_TRADE"], annotator="g",
                          would_execute=False, location_quality=2, note="premium long, poor location")
    assert a["would_execute"] is False and a["location_quality"] == 2
    b = A.make_annotation(candidate_id="c", decisions=["ACCEPT", "LONG"], annotator="g",
                          would_execute=True, location_quality=5)
    assert b["would_execute"] is True and b["location_quality"] == 5
    for bad in ({"location_quality": 0}, {"location_quality": 6}, {"would_execute": "yes"}):
        with pytest.raises(ValueError):
            A.make_annotation(candidate_id="c", decisions=["LONG"], annotator="g", **bad)


def test_reason_for_pass_labels():
    a = A.make_annotation(candidate_id="c", decisions=["REJECT", "NO_TRADE"], annotator="g",
                          would_execute=False,
                          reason_for_pass=["premium_discount", "rr_misleading"])
    assert a["reason_for_pass"] == ["premium_discount", "rr_misleading"]
    # unknown reason rejected
    with pytest.raises(ValueError):
        A.make_annotation(candidate_id="c", decisions=["NO_TRADE"], annotator="g",
                          reason_for_pass=["bad_vibes"])
    # 'other' requires a note
    with pytest.raises(ValueError):
        A.make_annotation(candidate_id="c", decisions=["NO_TRADE"], annotator="g",
                          reason_for_pass=["other"])
    ok = A.make_annotation(candidate_id="c", decisions=["NO_TRADE"], annotator="g",
                           reason_for_pass=["other"], note="thin liquidity into the close")
    assert ok["reason_for_pass"] == ["other"]
    # default is an empty list, never None
    d = A.make_annotation(candidate_id="c", decisions=["LONG"], annotator="g")
    assert d["reason_for_pass"] == []


def test_fidelity_and_outcome_datasets_are_disjoint():
    # a fidelity annotation shares NO structural keys with an outcome payload except the id link
    a = A.make_annotation(candidate_id="c", decisions=["LONG"], annotator="g")
    assert OUTCOME_TOP_KEYS.isdisjoint(a.keys())
    assert a["type"] == "human_fidelity"                 # explicitly a different dataset
