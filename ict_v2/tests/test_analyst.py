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
    g = AN.grade({"nearest": None}, {"sweep": None}, {}, {"stages": {"second_break": False}}, None)
    assert g["grade"] is None


def test_analyze_shape_and_verdict_is_valid():
    def bar(o, h, l, c):
        return {"o": o, "h": h, "l": l, "c": c, "t": None}
    # simple ramps so the detectors run without error
    def ramp(n):
        return [bar(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(n)]
    series = {"30m": ramp(40), "15m": ramp(60), "5m": ramp(80), "1m": ramp(120)}
    out = AN.analyze(series, "CME_MINI:MNQZ2026")
    assert out["verdict"] in ("🟢 Entry", "🟡 Hold", "🔴 No Trade")
    assert out["lines"][0] == out["verdict"] and isinstance(out["lines"], list) and len(out["lines"]) >= 4
    assert "disclaimer" in out
