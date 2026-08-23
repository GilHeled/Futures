"""Exit-analysis diagnostics: per-trade fields from an outcome payload + bars, and the aggregation
that separates entry-quality / exit-model / management questions."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import outcomes as OUT
from ict_live.journal import exit_analysis as EX
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _bar(i, o, h, l, c):
    t = datetime(2026, 6, 2, 9, 0, tzinfo=ET) + timedelta(hours=i)
    return Bar("1H", t, t + timedelta(hours=1), o, h, l, c, 100.0)


def test_diagnostics_stopped_after_running_favorably():
    # LONG entry 100, stop 99 (risk 1), target 110. Price runs to 103 (MFE +3R) then reverses to stop.
    bars = [_bar(0, 100, 100, 100, 100),      # decision/fill bar (contains entry)
            _bar(1, 100, 103, 100, 102),      # +3R MFE high
            _bar(2, 102, 102, 98.9, 99.0)]    # hits stop 99
    outcome = OUT.label_setup(id="s", symbol="MES", tf="1H", direction="long", entry=100.0,
                              stop=99.0, target=110.0, decision_index=0, bars=bars)
    d = EX.diagnostics(outcome, bars, direction="long", entry=100.0, stop=99.0, target=110.0, risk=1.0)
    assert d["triggered"] and d["outcome"] == "STOP"
    assert d["mfe_R"] == 3.0 and d["result_R"] == -1.0
    assert d["bars_to_mfe"] == 1 and d["bars_to_stop"] == 2
    assert d["reward_R"] == 10.0                    # distant target = 10R
    assert d["target_before_stop"] is False


def test_analyze_separates_entry_and_management():
    # three TRADEs: two stopped but had run to +2R MFE (management signal), one hit target
    rows = [
        {"execution": "TRADE", "triggered": True, "outcome": "STOP", "result_R": -1.0,
         "reward_R": 10.0, "mfe_R": 2.5, "mae_R": -1.0, "bars_to_mfe": 2, "bars_to_stop": 5,
         "bars_to_final": 5, "bars_to_target": None, "r1_before_stop": True, "r2_before_stop": True,
         "r3_before_stop": False, "target_before_stop": False, "symbol": "MES", "direction": "LONG"},
        {"execution": "TRADE", "triggered": True, "outcome": "STOP", "result_R": -1.0,
         "reward_R": 8.0, "mfe_R": 2.1, "mae_R": -1.0, "bars_to_mfe": 1, "bars_to_stop": 4,
         "bars_to_final": 4, "bars_to_target": None, "r1_before_stop": True, "r2_before_stop": True,
         "r3_before_stop": False, "target_before_stop": False, "symbol": "MES", "direction": "LONG"},
        {"execution": "TRADE", "triggered": True, "outcome": "TARGET", "result_R": 9.0,
         "reward_R": 9.0, "mfe_R": 9.0, "mae_R": -0.3, "bars_to_mfe": 6, "bars_to_stop": None,
         "bars_to_final": 6, "bars_to_target": 6, "r1_before_stop": True, "r2_before_stop": True,
         "r3_before_stop": True, "target_before_stop": True, "symbol": "MNQ", "direction": "SHORT"},
    ]
    a = EX.analyze(rows, execution="TRADE")
    assert a["n"] == 3 and a["n_stopped"] == 2
    assert a["target_hit_ratio"] == round(1 / 3, 3)
    assert a["reached_2R_before_stop"] == 1.0                  # all three reached 2R before stop
    # every trade ran at least +2R in its favor -> entries were not "dead"
    assert a["entry_mfe>=1R"] == 1.0
    # both stopped trades had already reached +2R MFE -> management (not entry) is the lever
    assert a["stopped_that_reached_2R_mfe"] == 1.0
    assert a["mfe_R_distn"]["median"] >= 2.0


def test_analyze_empty():
    assert EX.analyze([], execution="TRADE")["n"] == 0
