"""Exit-model simulator: each of the five pre-registered exit rules resolves to the correct realized
R on hand-built price paths, including intrabar-ambiguity exclusion and no-fill."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.journal import exit_models as EM
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _b(i, o, h, l, c):
    t = datetime(2026, 6, 2, 9, tzinfo=ET) + timedelta(hours=i)
    return Bar("1H", t, t + timedelta(hours=1), o, h, l, c, 100.0)


# LONG entry 100, stop 99 (risk 1), structural target 110 (reward 10R).
E, S, T = 100.0, 99.0, 110.0


def test_runs_to_2R_then_reverses_to_stop():
    # path: fill@100, runs to 102 (+2R) and 103 (+3R) then collapses through stop
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 103, 100, 102), _b(2, 102, 102, 98.5, 99.0)]
    r = EM.realized_r(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert r["fixed_2R"] == ("TARGET", 2.0)          # +2R banked before the reversal
    assert r["fixed_3R"] == ("TARGET", 3.0)
    assert r["structural_target"] == ("STOP", -1.0)  # never reached 110 -> stopped
    assert r["be_after_1R"] == ("BE", 0.0)           # passed +1R -> stop to BE -> stopped at entry = 0R
    # partial: +2R hit -> 0.5*2=1.0 banked, remainder stopped at BE -> +1.0 total
    assert r["partial_runner"] == ("PARTIAL+BE", 1.0)


def test_runs_straight_to_structural_target():
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 111, 100, 110.5)]   # blasts to 110+
    r = EM.realized_r(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert r["fixed_2R"] == ("TARGET", 2.0)
    assert r["structural_target"] == ("TARGET", 10.0)
    assert r["be_after_1R"] == ("TARGET", 10.0)
    assert r["partial_runner"][0] == "TARGET" and r["partial_runner"][1] == 10.0


def test_immediate_stop_no_partial():
    bars = [_b(0, 100, 100.2, 100, 100), _b(1, 100, 100.3, 98.9, 99.0)]  # never +2R, stop hit
    r = EM.realized_r(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert r["fixed_2R"] == ("STOP", -1.0)
    assert r["structural_target"] == ("STOP", -1.0)
    assert r["partial_runner"] == ("STOP", -1.0)     # stopped before +2R -> no partial


def test_intrabar_ambiguous_excluded():
    # a single bar spans both stop (99) and +2R (102): ambiguous for fixed_2R
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 102.5, 98.9, 100)]
    r = EM.realized_r(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert r["fixed_2R"] == ("AMBIGUOUS", None)


def test_no_fill():
    # entry 100 never touched (price stays above)
    bars = [_b(0, 101, 102, 100.5, 101.5), _b(1, 101.5, 103, 101, 102)]
    r = EM.realized_r(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert all(v == ("NO_FILL", None) for v in r.values())


def test_short_side_mirrors():
    # SHORT entry 100, stop 101 (risk 1), target 90. Price drops to 98 (+2R) then rips to stop.
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 100, 97, 98), _b(2, 98, 101.5, 98, 101.2)]
    r = EM.realized_r(bars, 0, direction="short", entry=100.0, stop=101.0, target=90.0)
    assert r["fixed_2R"] == ("TARGET", 2.0)
    assert r["be_after_1R"] == ("BE", 0.0)
    assert r["partial_runner"] == ("PARTIAL+BE", 1.0)


def test_aggregation_counts_and_caps():
    rows = [
        {"execution": "TRADE", "models": {m: {"result": "TARGET", "R": 10.0} for m in EM.MODELS}},
        {"execution": "TRADE", "models": {m: {"result": "STOP", "R": -1.0} for m in EM.MODELS}},
        {"execution": "PASS", "models": {m: {"result": "AMBIGUOUS", "R": None} for m in EM.MODELS}},
    ]
    a = EM.analyze(rows, execution="TRADE")["structural_target"]
    assert a["scored"] == 2 and a["win_rate"] == 0.5
    assert a["expectancy_R"] == 4.5                  # (10-1)/2
    assert a["expectancy_capped5_R"] == 2.0          # (5-1)/2  -> fat tail tamed
