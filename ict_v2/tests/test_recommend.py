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
    # configurable / toggleable — a disabled filter is now SHOWN (greyed), not omitted
    f = {x["name"]: x for x in REC.evaluate_filters(rr=1.0, killzone="", cfg={"min_rr": 3.0})}
    assert f["≥3R"]["ok"] is False and not f["≥3R"].get("disabled")
    assert f["killzone"]["disabled"] is True             # killzone off → shown as disabled, not gating


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


def test_configure_validation_knobs():
    """The take/skip knobs are tunable (for validation), with faithful defaults restored after."""
    good = REC.evaluate_filters(rr=4.0, killzone="ny_am")
    assert REC.REQUIRE_RETRACE is True and REC.COURSE_FILTERS["min_rr"] == 2.0   # faithful defaults
    # by default an armed setup is WATCH
    assert REC.recommend(structure="valid", filters=good, entry_live=False)[0] == "WATCH"
    try:
        REC.configure(require_retrace=False, min_rr=1.0, killzone=False)
        # relaxed: armed setup now TAKE; killzone filter dropped; RR floor lowered
        assert REC.recommend(structure="valid", filters=good, entry_live=False) == ("TAKE", [])
        f = {x["name"]: x for x in REC.evaluate_filters(rr=1.0, killzone="")}
        assert f["≥1R"]["ok"] is True                     # RR floor lowered to 1
        assert f["killzone"]["disabled"] and f["retrace"]["disabled"]   # both relaxations shown as disabled
        # a disabled filter never causes SKIP
        assert REC.recommend(structure="valid",
                             filters=list(f.values()), entry_live=False)[0] == "TAKE"
    finally:
        REC.configure(require_retrace=True, min_rr=2.0, killzone=True)     # restore faithful defaults
    assert REC.REQUIRE_RETRACE is True and REC.COURSE_FILTERS["killzone"] is True
