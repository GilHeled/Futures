"""The semantic layer: Structure / Quality / Course-Filters / Recommendation (ict_v2/recommend.py)."""
from ict_v2 import recommend as REC


def test_course_filters_min_rr_and_killzone():
    # min-RR filter (default floor = 2.0; NOT a course-specified number — a user-set take/skip threshold)
    f = {x["name"]: x for x in REC.evaluate_filters(rr=4.0, killzone="ny_am")}
    assert f["≥2R"]["ok"] is True and f["killzone"]["ok"] is True
    assert REC.evaluate_filters(rr=2.0, killzone="ny_am")[0]["ok"] is True      # exactly 2R passes
    f = {x["name"]: x for x in REC.evaluate_filters(rr=1.5, killzone="ny_am")}
    assert f["≥2R"]["ok"] is False and "RR 1.5 < 2R" in f["≥2R"]["reason"]
    # no target / RR
    f = {x["name"]: x for x in REC.evaluate_filters(rr=None, killzone="ny_am")}
    assert f["≥2R"]["ok"] is False and "no liquidity target" in f["≥2R"]["reason"]
    f = {x["name"]: x for x in REC.evaluate_filters(rr=0, killzone="ny_am")}
    assert f["≥2R"]["ok"] is False
    # killzone filter
    f = {x["name"]: x for x in REC.evaluate_filters(rr=4.0, killzone="")}
    assert f["killzone"]["ok"] is False and "killzone" in f["killzone"]["reason"]
    # configurable / toggleable
    f = REC.evaluate_filters(rr=1.0, killzone="", cfg={"min_rr": 3.0})   # only ≥3R, killzone off
    assert [x["name"] for x in f] == ["≥3R"] and f[0]["ok"] is False


def test_recommendation_derivation():
    good = REC.evaluate_filters(rr=4.0, killzone="ny_am")
    assert REC.recommend(structure="valid", filters=good) == ("TAKE", [])
    # ~2R now passes the floor → TAKE (RR is a quality metric; the floor is a user-set take/skip filter)
    assert REC.recommend(structure="valid", filters=REC.evaluate_filters(rr=2.0, killzone="ny_am")) == ("TAKE", [])
    rec, reasons = REC.recommend(structure="valid", filters=REC.evaluate_filters(rr=1.5, killzone="ny_am"))
    assert rec == "SKIP" and any("≥2R" in r for r in reasons)           # below the floor → filtered out
    rec, reasons = REC.recommend(structure="valid", filters=REC.evaluate_filters(rr=4.0, killzone=""))
    assert rec == "SKIP" and any("killzone" in r for r in reasons)
    rec, reasons = REC.recommend(structure="invalid", structure_reason="entry mitigated")
    assert rec == "SKIP" and "mitigated" in reasons[0]
    rec, reasons = REC.recommend(structure="forming", structure_reason="no MSS yet")
    assert rec == "WATCH" and "no MSS yet" in reasons[0]
    assert set(REC.RECOMMENDATIONS) == {"TAKE", "SKIP", "WATCH"}


def test_armed_entry_is_watch_not_take():
    """A valid, filter-passing setup whose FVG is NOT yet retraced into (entry_live=False) is ARMED →
    WATCH ('waiting for retrace'), not TAKE. Once live it becomes TAKE (course §14: enter on retrace)."""
    good = REC.evaluate_filters(rr=4.0, killzone="ny_am")
    rec, reasons = REC.recommend(structure="valid", filters=good, entry_live=False)
    assert rec == "WATCH" and "retrace" in reasons[0].lower()
    assert REC.recommend(structure="valid", filters=good, entry_live=True) == ("TAKE", [])
    # a failing filter still SKIPs even if armed (filter check precedes the retrace check)
    bad = REC.evaluate_filters(rr=1.0, killzone="ny_am")
    assert REC.recommend(structure="valid", filters=bad, entry_live=False)[0] == "SKIP"
