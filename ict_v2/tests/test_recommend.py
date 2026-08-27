"""The semantic layer: Structure / Quality / Course-Filters / Recommendation (ict_v2/recommend.py)."""
from ict_v2 import recommend as REC


def test_course_filters_min_rr_and_killzone():
    # ≥3R filter
    f = {x["name"]: x for x in REC.evaluate_filters(rr=4.0, killzone="ny_am")}
    assert f["≥3R"]["ok"] is True and f["killzone"]["ok"] is True
    f = {x["name"]: x for x in REC.evaluate_filters(rr=2.0, killzone="ny_am")}
    assert f["≥3R"]["ok"] is False and "RR 2 < 3R" in f["≥3R"]["reason"]
    # no target / RR
    f = {x["name"]: x for x in REC.evaluate_filters(rr=None, killzone="ny_am")}
    assert f["≥3R"]["ok"] is False and "no liquidity target" in f["≥3R"]["reason"]
    f = {x["name"]: x for x in REC.evaluate_filters(rr=0, killzone="ny_am")}
    assert f["≥3R"]["ok"] is False
    # killzone filter
    f = {x["name"]: x for x in REC.evaluate_filters(rr=4.0, killzone="")}
    assert f["killzone"]["ok"] is False and "killzone" in f["killzone"]["reason"]
    # configurable / toggleable
    f = REC.evaluate_filters(rr=1.0, killzone="", cfg={"min_rr": 2.0})   # only ≥2R, killzone off
    assert [x["name"] for x in f] == ["≥2R"] and f[0]["ok"] is False


def test_recommendation_derivation():
    good = REC.evaluate_filters(rr=4.0, killzone="ny_am")
    assert REC.recommend(structure="valid", filters=good) == ("TAKE", [])
    rec, reasons = REC.recommend(structure="valid", filters=REC.evaluate_filters(rr=2.0, killzone="ny_am"))
    assert rec == "SKIP" and any("≥3R" in r for r in reasons)           # valid setup, filtered out
    rec, reasons = REC.recommend(structure="valid", filters=REC.evaluate_filters(rr=4.0, killzone=""))
    assert rec == "SKIP" and any("killzone" in r for r in reasons)
    rec, reasons = REC.recommend(structure="invalid", structure_reason="entry mitigated")
    assert rec == "SKIP" and "mitigated" in reasons[0]
    rec, reasons = REC.recommend(structure="forming", structure_reason="no MSS yet")
    assert rec == "WATCH" and "no MSS yet" in reasons[0]
    assert set(REC.RECOMMENDATIONS) == {"TAKE", "SKIP", "WATCH"}
