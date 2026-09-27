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


def test_grade_A_needs_completed_trigger_and_room():
    g = AN.grade({"nearest": {"x": 1}, "confluence": 0}, False, True, 2.5)
    assert g["grade"] == "A" and g["trigger_completed"] is True


def _b(c, l=None, h=None, t=None):
    return {"o": c, "h": h if h is not None else c + 1, "l": l if l is not None else c - 1, "c": c, "t": t}


def test_acceptance_needs_two_consecutive_closes_not_a_wick():
    # invalidated: >=2 consecutive completed closes below the level
    assert AN._acceptance([_b(100), _b(98), _b(97), _b(101)], 0, 100.0, below=True)[0] == "invalidated"
    # weakened: only ONE close below (run resets) — a single close is NOT acceptance
    assert AN._acceptance([_b(100), _b(98), _b(101), _b(102)], 0, 100.0, below=True)[0] == "weakened"
    # weakened: a WICK pierced but it held on a close basis
    assert AN._acceptance([_b(100), _b(101, l=99), _b(102)], 0, 100.0, below=True)[0] == "weakened"
    # active: price never traded below it
    assert AN._acceptance([_b(100), _b(102, l=101), _b(103, l=102)], 0, 100.0, below=True)[0] == "active"
    # resistance side (long path): two consecutive closes ABOVE invalidate
    assert AN._acceptance([_b(100), _b(102), _b(103), _b(99)], 0, 100.0, below=False)[0] == "invalidated"


def test_obstacle_scan_shape_and_causality():
    def ramp(n):
        return [_b(100 + i, l=99 + i, h=101 + i) for i in range(n)]
    series = {"15m": ramp(40), "5m": ramp(60), "1m": ramp(90)}
    out = AN.obstacle_scan(series, "SHORT", entry=200.0, stop_pts=10.0)   # entry above all -> supports below
    assert set(out) == {"rows", "first"}
    for r in out["rows"]:
        assert r["price"] < 200.0 and r["status"] in ("active", "weakened", "invalidated") and r["R"] > 0
    if out["first"]:
        assert out["first"]["status"] != "invalidated"                    # first is never an excluded level
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
    actives = [r for r in out["rows"] if r["status"] != "invalidated"]
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


def test_frozen_snapshot_obstacle_is_reproducible():
    """ACCEPTANCE TEST — replay the exact frozen MNQZ2026 snapshot (future candles unavailable) and pin
    the ordered obstacle scan + first active obstacle + grade, so a later revision cannot silently move
    the obstacle down again without new evidence (i.e. without updating this fixture on purpose)."""
    import json, os, datetime as _dt
    fx = os.path.join(os.path.dirname(__file__), "fixtures", "frozen_snapshot.json")
    if not os.path.exists(fx):
        import pytest
        pytest.skip("frozen fixture not generated")
    data = json.load(open(fx))
    now = _dt.datetime.fromisoformat(data["now"])
    out = AN.analyze(data["series"], data["symbol"], now=now)
    rows = out["obstacle_scan"]
    # rows are ordered nearest-first (short -> descending price)
    assert [r["price"] for r in rows] == sorted((r["price"] for r in rows), reverse=True)
    # the first active obstacle is the first NON-invalidated row (never a farther, better-R level)
    first_active = next((r for r in rows if r["status"] != "invalidated"), None)
    assert first_active is not None and out["first_obstacle"] == first_active["price"]
    # every excluded level carries acceptance evidence + a timestamp (no silent skipping)
    for r in rows:
        if r["status"] == "invalidated":
            assert "consecutive closes" in r["evidence"] and r["ev_from"]
    # the pinned outcome for THIS snapshot: 0.17R room, no A/B grade, WATCH
    assert out["first_obstacle"] == 30914.25 and out["effective_R"] == 0.17
    assert out["grade"] is None and out["state"] == "WATCH"


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
