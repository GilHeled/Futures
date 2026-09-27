"""MNQ analyst model — deterministic sizing/table + report shape. Read-only decision-support."""
from ict_v2 import analyst as AN


def test_sizing_two_contracts_within_37_5():
    r = AN.risk_size(entry=100.0, stop=70.0, targets=[160.0])   # 30pt stop
    assert r["available"] and r["contracts"] == 2 and r["stop_pts"] == 30.0
    assert r["risk_per_account"] == 120.0                       # 30 * 2 * 2
    assert r["R_to_targets"] == [2.0]


def test_sizing_one_contract_between_37_5_and_75():
    r = AN.risk_size(entry=100.0, stop=140.0, targets=None)     # 40pt stop
    assert r["available"] and r["contracts"] == 1 and r["stop_pts"] == 40.0
    assert r["risk_per_account"] == 80.0                        # 40 * 2 * 1


def test_sizing_no_trade_beyond_75():
    r = AN.risk_size(entry=100.0, stop=180.0, targets=None)     # 80pt stop
    assert not r["available"] and r["contracts"] == 0 and "no trade" in r["reason"]


def test_sizing_boundary_37_5_is_two_contracts_at_150():
    r = AN.risk_size(entry=100.0, stop=137.5, targets=None)     # exactly 37.5pt
    assert r["contracts"] == 2 and r["risk_per_account"] == 150.0 and r["available"]


def test_tick_rounding_is_valid_and_directional():
    assert AN._tick(30939.63, "up") == 30939.75      # short stop rounds AWAY (up) to a valid tick
    assert AN._tick(30939.63, "down") == 30939.5      # long stop rounds AWAY (down)
    assert AN._tick(30918.1, "near") == 30918.0
    for v in (AN._tick(30939.63, "up"), AN._tick(30845.13, "up"), AN._tick(30684.4, "near")):
        assert round((v / 0.25) - round(v / 0.25), 6) == 0     # exact 0.25 multiple


def test_sizing_conditional_when_no_levels():
    r = AN.risk_size(entry=None, stop=None, targets=None)
    assert not r["available"] and "conditional" in r["reason"]


def test_short_stop_sits_above_swept_high():
    # supply OB with a BSL sweep ABOVE the OB top -> stop must be above the sweep, not the OB top
    nd = {"type": "OB", "dir": "supply", "top": 30930.75, "bottom": 30918.0, "ref": 30924.0}
    sweep = {"side": "BSL", "price": 30935.25, "mitigated": False}
    stop = AN.stop_level("SHORT", nd, sweep, buffer=2.0)
    assert stop == 30937.25 and stop > sweep["price"]


def test_long_stop_sits_below_swept_low():
    nd = {"type": "OB", "dir": "demand", "top": 30918.0, "bottom": 30905.0, "ref": 30911.0}
    sweep = {"side": "SSL", "price": 30900.0, "mitigated": False}
    stop = AN.stop_level("LONG", nd, sweep, buffer=2.0)
    assert stop == 30898.0 and stop < sweep["price"]


def test_stop_falls_back_to_ob_when_no_relevant_sweep():
    nd = {"type": "OB", "dir": "supply", "top": 30930.75, "bottom": 30918.0, "ref": 30924.0}
    # a sweep on the wrong side (SSL) must not pull a short's stop
    assert AN.stop_level("SHORT", nd, {"side": "SSL", "price": 30800.0}) == 30932.75


def test_grade_none_without_location():
    assert AN.grade({"nearest": None}, False, False, None)["grade"] is None


def test_grade_none_when_trigger_pending():
    # location present + good room, but trigger NOT completed -> no grade, WATCH
    g = AN.grade({"nearest": {"x": 1}, "confluence": 1}, True, False, 3.0)
    assert g["grade"] is None and g["trigger_completed"] is False


def test_grade_A_needs_completed_trigger_room_and_target():
    # A requires >=2R room AND a >=2R principal target (and clear context + quality location, default True)
    g = AN.grade({"nearest": {"x": 1}, "confluence": 0}, False, True, 2.5, principal_R=2.5)
    assert g["grade"] == "A" and g["trigger_completed"] is True
    # same room but principal target < 2R -> not A (a remote TP cannot be assumed)
    assert AN.grade({"nearest": {"x": 1}, "confluence": 0}, False, True, 2.5, principal_R=1.2)["grade"] == "B"


def _b(c, l=None, h=None, t=None):
    return {"o": c, "h": h if h is not None else c + 1, "l": l if l is not None else c - 1, "c": c, "t": t}


def test_acceptance_needs_two_consecutive_closes_not_a_wick():
    # CLEARED: >=2 consecutive completed closes below the level
    assert AN._acceptance([_b(100), _b(98), _b(97), _b(101)], 0, 100.0, below=True)[0] == "CLEARED"
    # WEAKENED: only ONE close below (run resets) — a single close is NOT acceptance
    assert AN._acceptance([_b(100), _b(98), _b(101), _b(102)], 0, 100.0, below=True)[0] == "WEAKENED"
    # WEAKENED: a WICK pierced but it held on a close basis
    assert AN._acceptance([_b(100), _b(101, l=99), _b(102)], 0, 100.0, below=True)[0] == "WEAKENED"
    # ACTIVE: price never traded below it
    assert AN._acceptance([_b(100), _b(102, l=101), _b(103, l=102)], 0, 100.0, below=True)[0] == "ACTIVE"
    # UNKNOWN: no completed bars after formation -> clearance unverifiable
    assert AN._acceptance([_b(100)], 0, 100.0, below=True)[0] == "UNKNOWN"
    # resistance side (long path): two consecutive closes ABOVE -> CLEARED
    assert AN._acceptance([_b(100), _b(102), _b(103), _b(99)], 0, 100.0, below=False)[0] == "CLEARED"


def test_obstacle_scan_shape_and_causality():
    def ramp(n):
        return [_b(100 + i, l=99 + i, h=101 + i) for i in range(n)]
    series = {"15m": ramp(40), "5m": ramp(60), "1m": ramp(90)}
    out = AN.obstacle_scan(series, "SHORT", entry=200.0, stop_pts=10.0)   # entry above all -> supports below
    assert set(out) == {"rows", "first"}
    for r in out["rows"]:
        assert r["price"] < 200.0 and r["status"] in ("ACTIVE", "WEAKENED", "CLEARED", "UNKNOWN", "TRIGGER_THRESHOLD") and r["R"] > 0
    if out["first"]:
        assert out["first"]["status"] in ("ACTIVE", "WEAKENED", "UNKNOWN")   # first still obstructs
    # ordering: rows are nearest-first (descending price for a short)
    prices = [r["price"] for r in out["rows"]]
    assert prices == sorted(prices, reverse=True)
    assert AN.obstacle_scan(series, "SHORT", None, 10.0) == {"rows": [], "first": None}


def test_obstacle_scan_does_not_skip_nearer_active_for_better_R():
    # two supports below a short entry: a NEARER active one and a FARTHER active one (better R).
    # the scan must return the NEARER as `first`, never the farther just because its R is larger.
    def series_with_two_supports():
        # build a 5m series with pivot lows at ~150 (near) and ~120 (far), both held (active)
        seq = [200, 198, 196, 194, 192, 190,          # descend
               150,                                     # pivot low (near support)
               160, 165, 170, 168, 166,                # bounce, holds above 150
               155, 152,                                # dip toward 150 but close above
               158, 162, 164, 166, 168,
               120,                                     # deeper pivot low (far support)
               130, 140, 145, 150, 155, 160, 165, 170] # recover, both lows held on a close basis
        return [_b(v, l=v - 1, h=v + 1) for v in seq]
    series = {"15m": [], "5m": series_with_two_supports(), "1m": []}
    out = AN.obstacle_scan(series, "SHORT", entry=210.0, stop_pts=10.0)
    actives = [r for r in out["rows"] if r["status"] != "CLEARED" and r["meaningful"]]
    if len(actives) >= 2 and out["first"]:
        # the first active obstacle is the highest-priced active level (nearest to entry)
        assert out["first"]["price"] == max(r["price"] for r in actives)


def test_location_marks_ob_valid_and_confirmed_by():
    # a ramp with a pullback that forms an order block; assert the status axis is present and distinct
    def bar(o, h, l, c):
        return {"o": o, "h": h, "l": l, "c": c, "t": None}
    b5 = [bar(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(80)]
    out = AN.location(b5, price=b5[-1]["c"])
    nd = out["nearest"]
    if nd and nd["type"] == "OB":
        assert nd["valid"] is True and nd["status"] == "active" and "displacement" in nd["confirmed_by"]


def _load_frozen():
    import json, os
    fx = os.path.join(os.path.dirname(__file__), "fixtures", "frozen_snapshot.json")
    if not os.path.exists(fx):
        import pytest
        pytest.skip("frozen fixture not generated")
    return json.load(open(fx))


def test_frozen_snapshot_trigger_is_rejected_no_continuation():
    """ACCEPTANCE TEST — replay the exact frozen MNQZ2026 snapshot (future candles unavailable). The
    candidate break→retest→'second break' does NOT continue past the first low (30,889.75): the market
    rallied and an opposing break occurred between the two. The trigger must therefore be REJECTED
    (PENDING), publish NO executable entry/stop, and expose timestamped candle evidence for why."""
    import datetime as _dt
    data = _load_frozen()
    out = AN.analyze(data["series"], data["symbol"], now=_dt.datetime.fromisoformat(data["now"]))
    # this snapshot must NOT produce a false executable order, and READY setup is never execution-cleared
    assert out["state"] != "READY" and out["setup_qualified"] is False and out["execution_cleared"] is False
    # the SHORT break→retest→'second break' that fooled the old detector is REJECTED (opposing break reversed
    # the leg) — test the detector DIRECTLY so location selection can't mask the regression
    b1 = data["series"]["1m"]
    seq = AN.trigger_sequence(b1, "SHORT", buffer=2.0)
    assert seq["completed"] is False and seq["rejected_reason"]
    assert seq["break"]["level"] == 30889.75 and seq["second_break"]["level"] == 30914.25
    assert seq["continuation"]["opposing_break_between"] is True
    assert "opposing structural break" in seq["rejected_reason"]
    # same snapshot but STALE -> NO TRADE / STALE_DATA (staleness is a separate, top-line reason)
    stale = AN.analyze(data["series"], data["symbol"], now=_dt.datetime.fromisoformat(data["now"]) + _dt.timedelta(days=1))
    assert stale["state"] == "NO_TRADE" and stale["reason_code"] == "STALE_DATA"


def _load_fixture(name):
    import json, os
    fx = os.path.join(os.path.dirname(__file__), "fixtures", name + ".json")
    if not os.path.exists(fx):
        import pytest
        pytest.skip(f"fixture {name} not generated")
    return json.load(open(fx))


def _run_fixture(name):
    import datetime as _dt
    d = _load_fixture(name)
    return AN.analyze(d["series"], d["symbol"], now=_dt.datetime.fromisoformat(d["now"]))


def test_state_completed_trigger_insufficient_room_is_no_trade():
    """A COMPLETED, evidenced 1m trigger with < 2R to the first meaningful obstacle -> NO TRADE."""
    o = _run_fixture("insuff_room")
    assert o["triggered"] is True and o["trigger_sequence"]["completed"] is True
    assert o["entry"] == 29922.0 and o["effective_R"] == 0.07
    assert o["state"] == "NO_TRADE" and o["grade"] is None
    room = next(g for g in o["gates"] if g["name"].startswith("≥"))
    assert room["ok"] is False                           # room is the failing gate
    assert next(g for g in o["gates"] if g["name"].startswith("trigger route"))["ok"] is True


def test_state_completed_trigger_with_room_is_ready_A():
    """A COMPLETED trigger with CLEAR (aligned) HTF context, quality location, structural stop, permitted
    risk and >= 2R before the first meaningful obstacle -> READY / grade A."""
    o = _run_fixture("ready_A")
    seq = o["trigger_sequence"]
    assert o["triggered"] is True and o["entry"] == 30867.5 and o["effective_R"] == 4.03
    assert o["direction"] == "LONG" and o["context"]["bias30"] == "bullish" and o["context_clear"] is True
    assert o["entry"] != 30918.0 and o["stop"] != 30939.75      # rebuilt from the trigger
    assert seq["continuation"]["continued_past_retest"] is True
    assert o["state"] == "READY" and o["detailed_state"] == "PLAN_VALIDATED->WAITING_FOR_FILL" and o["grade"] == "A"
    assert o["setup_qualified"] is True and o["execution_cleared"] is False
    assert o["stop_thesis_id"] in ("LOCAL_1M_TRIGGER", "FULL_5M_SETUP") and o["stop_invalidates"]
    assert {"LOCAL_1M_TRIGGER", "FULL_5M_SETUP"} <= set(o["stop_options"])          # both stop theses offered
    assert {e["method"] for e in o["entry_options"]} == {"confirmed_continuation", "retest_limit"}
    # every MECHANICAL gate passes; the execution-clearance gates stay UNKNOWN (None)
    mech = [g for g in o["gates"] if g["ok"] is not None]
    assert all(g["ok"] is True for g in mech)
    assert o["execution_cleared"] is False       # READY setup is NOT an execution clearance


def test_state_invalidation_breached_before_entry_is_no_trade():
    """A COMPLETED trigger whose stop is already breached by current price -> NO TRADE (premise void)."""
    o = _run_fixture("inval_breached")
    assert o["triggered"] is True
    inval = next(g for g in o["gates"] if g["name"].startswith("invalidation"))
    assert inval["ok"] is False and "beyond stop" in inval["detail"]
    assert o["state"] == "NO_TRADE" and o["grade"] is None


def test_location_audit_reports_selected_zone_and_candidate_status():
    """The SELECTED 5m location is auditable: zone, candidate-vs-confirmed, and 30m relationship."""
    o = _run_fixture("ready_A")
    la = o["location_audit"]
    assert la is not None and la["zone"] and la["dir"] in ("supply", "demand")
    assert "confirmed_confluence" in la and isinstance(la["aligned_with_30m"], bool)
    # a conf-0 location is classified a CANDIDATE, never confirmed confluence
    frozen = _run_fixture("insuff_room")["location_audit"]
    if frozen and frozen["confluence"] == 0:
        assert frozen["confirmed_confluence"] is False and "candidate" in frozen["classification"]


def test_trigger_sequence_completes_only_on_real_continuation():
    def bar(o, h, l, c):
        return {"o": o, "h": h, "l": l, "c": c, "t": None}
    # VALID short continuation: break L1, lower-high retest, then a LOWER LOW that CLOSES below L1
    vals = [104, 106, 108, 107, 105, 103, 101, 100, 102, 104, 98, 101, 103, 102, 99, 96, 94]
    good = [bar(v, v + 1, v - 1, v) for v in vals]
    tr = AN.trigger_sequence(good, "SHORT", buffer=2.0, recency=20)
    assert tr["completed"] is True and tr["continuation"]["continued_past_retest"] is True
    assert tr["entry"] is not None and tr["stop"] > tr["entry"]        # short stop sits ABOVE entry


def test_trigger_sequence_rejects_when_no_close_below_first_low():
    def bar(o, h, l, c):
        return {"o": o, "h": h, "l": l, "c": c, "t": None}
    # break L1=100, retest, then a shallow dip that never closes below 100 (no continuation)
    vals = [104, 106, 108, 107, 105, 103, 101, 100, 102, 104, 98, 102, 106, 104, 103, 102, 101]
    bad = [bar(v, v + 1, v - 1, v) for v in vals]
    tr = AN.trigger_sequence(bad, "SHORT", buffer=2.0, recency=20)
    assert tr["completed"] is False and tr["rejected_reason"]


def test_rule_inventory_three_categories():
    inv = AN.rule_inventory()
    assert set(("approved", "provisional", "observed_evidence_classes", "unavailable_in_this_env")) <= set(inv)
    # provisional heuristics are named with rationale + range (not passed off as approved/derived)
    for k in ("TRIGGER_RECENCY", "STOP_FLOOR_MULT", "ACCEPT_CLOSES"):
        assert k in inv["provisional"] and "rationale" in inv["provisional"][k] and "range" in inv["provisional"][k]
    assert inv["approved"]["min_R_A"] == 2.0 and inv["approved"]["per_trade_risk_usd"] == 150.0


def test_completed_B_trigger_is_preserved_not_dropped():
    """A completed-trigger B-quality plan is recorded as grade B, trigger CONFIRMED, watch-only /
    execution prohibited — NOT reported as pending/invalid and never execution_cleared (spec §2)."""
    o = _run_fixture("b_watch")
    assert o["grade"] == "B" and o["trigger_confirmed"] is True
    assert o["state"] == "WATCH" and o["detailed_state"].startswith("TRIGGER_CONFIRMED")
    assert o["setup_qualified"] is False and o["execution_cleared"] is False
    assert "watch-only" in o["reason"] and "Deficiency" in o["reason"]


def test_target_candidates_have_source_tf_and_edges():
    o = _run_fixture("ready_A")
    tc = o["target_candidates"]
    assert tc and all({"source", "tf", "price", "dist_pts", "R", "role"} <= set(c) for c in tc)
    sources = {c["source"] for c in tc}
    assert any("pool" in s or "OB" in s or "range" in s or "EQ" in s for s in sources)
    # an optional partial exit only ever exists with 2 MNQ and only in the 1.5–2R band
    pe = o["partial_exit"]
    assert pe is None or (1.5 <= pe["R"] < 2.0 and o["risk"]["contracts"] >= 2)


def test_targets_from_structure_not_only_external():
    o = _run_fixture("ready_A")
    # targets are an ORDERED list of price-dependent draws in the trade direction
    assert isinstance(o["targets_ordered"], list) and o["tp1"] is not None
    if o["direction"] == "LONG":
        assert all(x > o["entry"] for x in o["targets_ordered"])
    else:
        assert all(x < o["entry"] for x in o["targets_ordered"])


def test_execution_gates_unknown_and_ready_is_not_cleared():
    o = _run_fixture("ready_A")           # no account/news supplied -> UNKNOWN, execution blocked
    names = [g["name"] for g in o["gates"]]
    assert any("news window" in n for n in names) and any("XFAs" in n for n in names)
    for g in o["gates"]:
        if "news window" in g["name"] or "XFAs" in g["name"]:
            assert g["ok"] is None                 # UNKNOWN when data not supplied
    assert o["setup_qualified"] is True and o["execution_cleared"] is False
    assert o["reason_code"] == "READY_SETUP_EXECUTION_BLOCKED"


def test_execution_cleared_when_account_and_news_supplied():
    import json, datetime as _dt
    d = _load_fixture("ready_A")
    good = {"xfa1": {"realized_loss_today": 0, "open_risk": 0, "fees": 0, "trades_today": 0,
                     "copier_ok": True, "protection_ok": True},
            "xfa2": {"realized_loss_today": 0, "open_risk": 0, "fees": 0, "trades_today": 0,
                     "copier_ok": True, "protection_ok": True}}
    o = AN.analyze(d["series"], d["symbol"], now=_dt.datetime.fromisoformat(d["now"]),
                   account=good, news={"clear": True})
    assert o["state"] == "READY" and o["execution_cleared"] is True and o["reason_code"] == "READY_ORDER"


def test_daily_rules_block_when_account_fails():
    import datetime as _dt
    d = _load_fixture("ready_A")
    # xfa2 already used its one trade today -> the copied trade is blocked
    acct = {"xfa1": {"trades_today": 0, "copier_ok": True, "protection_ok": True},
            "xfa2": {"trades_today": 1, "copier_ok": True, "protection_ok": True}}
    o = AN.analyze(d["series"], d["symbol"], now=_dt.datetime.fromisoformat(d["now"]),
                   account=acct, news={"clear": True})
    assert o["state"] == "NO_TRADE" and o["execution_cleared"] is False
    # a PDLL-exhausted account also blocks (headroom < planned risk)
    acct2 = {"xfa1": {"realized_loss_today": 290.0, "trades_today": 0, "copier_ok": True, "protection_ok": True},
             "xfa2": {"realized_loss_today": 0, "trades_today": 0, "copier_ok": True, "protection_ok": True}}
    o2 = AN.analyze(d["series"], d["symbol"], now=_dt.datetime.fromisoformat(d["now"]),
                    account=acct2, news={"clear": True})
    assert o2["state"] == "NO_TRADE" and "PDLL" in (o2["account_gate"].get("detail") or "")


def test_account_gate_headroom_and_weaker_account():
    g = AN.account_gate({"a": {"realized_loss_today": 0, "trades_today": 0},
                         "b": {"realized_loss_today": 200.0, "trades_today": 0}},
                        planned_risk_per_acct=100.0, planned_contracts=2)
    assert g["known"] is True and g["ok"] is False        # weaker account 'b' lacks headroom
    assert g["max_contracts_by_budget"] < 2               # weaker account binds size down
    assert AN.account_gate(None, 100.0, 2)["ok"] is None   # unknown -> UNKNOWN


def test_stop_options_offers_both_theses_distinctly():
    trg = {"stop": 30923.75, "retest": {"price": 30921.75}}
    nd = {"type": "OB", "dir": "supply", "top": 30930.75, "bottom": 30918.0, "ref": 30924.0}
    opts = AN.stop_options("SHORT", trg, nd, {"side": "BSL", "price": 30935.25, "mitigated": False}, buffer=2.0)
    assert {"LOCAL_1M_TRIGGER", "FULL_5M_SETUP"} <= set(opts)
    assert opts["LOCAL_1M_TRIGGER"]["price"] == 30923.75         # beyond the 1m retest extreme
    assert opts["FULL_5M_SETUP"]["price"] == 30937.25            # beyond max(OB top, swept BSL) + buffer
    assert opts["LOCAL_1M_TRIGGER"]["price"] != opts["FULL_5M_SETUP"]["price"]
    assert "retest" in opts["LOCAL_1M_TRIGGER"]["invalidates"] and "5m" in opts["FULL_5M_SETUP"]["invalidates"]


def test_revalidate_at_fill_paths():
    a = {"direction": "SHORT", "stop": 100.0, "tp1": 80.0, "tp2": 70.0}
    # eligible fill (R=3) but no account/news -> PLAN_VALIDATED / WAITING_FOR_FILL, NOT execution-cleared
    r = AN.revalidate_at_fill(a, 95.0)
    assert r["eligible"] and r["execution_cleared"] is False and r["state"] == "WAITING_FOR_FILL"
    # with account + news OK -> IN_POSITION
    r2 = AN.revalidate_at_fill(a, 95.0, account_ok=True, news_ok=True)
    assert r2["execution_cleared"] is True and r2["state"] == "IN_POSITION"
    # fill already beyond the stop -> rejected
    assert AN.revalidate_at_fill(a, 101.0)["reason_code"] == "INVALIDATION_BREACHED"
    # fill where R deteriorated below 2R -> rejected
    assert AN.revalidate_at_fill(a, 88.0)["reason_code"] == "INADEQUATE_ROOM"


def test_grade_A_plus_and_B_and_risk_ceiling_invariants():
    # B when context not clear even with room+target (watch-only)
    assert AN.grade({"nearest": {"x": 1}, "confluence": 0}, False, True, 2.5,
                    context_clear=False, location_quality_ok=True, principal_R=3.0)["grade"] == "B"
    # A+ needs conf>=1 and >=2.5R room+target
    assert AN.grade({"nearest": {"x": 1}, "confluence": 1}, True, True, 3.0, principal_R=3.0)["grade"] == "A+"
    # sizing is independent of grade: A+ still respects the $150 ceiling and never 3 MNQ
    r = AN.risk_size(entry=100.0, stop=110.0, targets=[130.0])
    assert r["risk_per_account"] <= 150.0 and r["contracts"] <= 2


def test_trigger_threshold_is_nearest_swing_low_below_for_short():
    lows = [110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 110]   # strict pivot low at idx5 = 100
    b1 = [_b(v + 1, l=v, h=v + 2) for v in lows]
    th = AN.trigger_threshold(b1, "SHORT", price=109.0)
    assert th and th["level"] < 109.0 and "close below" in th["must"]
    assert AN.trigger_threshold([], "SHORT", 100.0) is None


def test_analyze_shape_and_verdict_is_valid():
    def bar(o, h, l, c):
        return {"o": o, "h": h, "l": l, "c": c, "t": None}
    # simple ramps so the detectors run without error
    def ramp(n):
        return [bar(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(n)]
    series = {"30m": ramp(40), "15m": ramp(60), "5m": ramp(80), "1m": ramp(120)}
    out = AN.analyze(series, "CME_MINI:MNQZ2026")
    assert out["verdict"] in ("🟢 Ready", "🟡 Watch", "🔴 No Trade")
    assert out["state"] in ("READY", "WATCH", "NO_TRADE")
    assert out["lines"][0] == out["verdict"] and isinstance(out["lines"], list) and len(out["lines"]) >= 4
    assert "disclaimer" in out
