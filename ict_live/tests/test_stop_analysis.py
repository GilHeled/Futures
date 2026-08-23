"""Stop-analysis: the excursion split around the first −1R stop and the three-bucket classification
resolve correctly on hand-built paths (true invalidation / premature / late continuation)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.journal import stop_analysis as SP
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _b(i, o, h, l, c):
    t = datetime(2026, 6, 2, 9, tzinfo=ET) + timedelta(hours=i)
    return Bar("1H", t, t + timedelta(hours=1), o, h, l, c, 100.0)


# LONG entry 100, stop 99 (risk 1), structural target 110.
E, S, T = 100.0, 99.0, 110.0


def test_premature_stop_then_quick_recovery():
    # fill, dip to stop (−1R) at bar1, then recover to +2R at bar3 (2 bars after stop = "shortly")
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 100, 98.8, 99.0),
            _b(2, 99, 100.5, 99, 100), _b(3, 100, 102.2, 100, 102)]
    m = SP.measure_stop(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert m["stopped"] and m["bucket"] == "premature_stop"
    assert m["t_entry_to_stop"] == 1 and m["recovered"] and m["t_stop_to_recovery"] == 2
    assert m["mae_before_recovery_R"] >= 1.0            # it did dip through −1R
    assert m["mfe_after_stop"] >= 2.0


def test_true_invalidation_no_recovery():
    # dip to stop, then chops sideways below +2R forever
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 100, 98.8, 99.0)] + \
           [_b(i, 99.5, 100.2, 99.3, 99.8) for i in range(2, 12)]
    m = SP.measure_stop(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert m["stopped"] and m["recovered"] is False and m["bucket"] == "true_invalidation"
    assert m["target_after_stop"] is False


def test_late_continuation_far_after_stop():
    # dip to stop at bar1, then only recovers to +2R at bar 1+13 (>12 bars = late)
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 100, 98.8, 99.0)]
    bars += [_b(i, 99.5, 99.9, 99.2, 99.6) for i in range(2, 14)]      # long quiet stretch
    bars += [_b(14, 99.6, 102.3, 99.6, 102)]                          # recovery at bar 14 (13 after stop)
    m = SP.measure_stop(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert m["stopped"] and m["recovered"] and m["t_stop_to_recovery"] == 13
    assert m["bucket"] == "late_continuation"


def test_survived_never_stopped():
    bars = [_b(0, 100, 100, 100, 100), _b(1, 100, 101, 99.6, 100.8), _b(2, 100.8, 102.5, 100.5, 102)]
    m = SP.measure_stop(bars, 0, direction="long", entry=E, stop=S, target=T)
    assert m["stopped"] is False and m["bucket"] == "survived"
    assert m["mfe_before_stop"] == m["eventual_mfe_R"]


def test_analyze_bucket_shares():
    rows = [
        {"symbol": "MES", "execution": "TRADE", "stopped": True, "eventual_mfe_R": 3.0,
         "bucket": "premature_stop", "mfe_before_stop": 0.5, "mfe_after_stop": 3.0,
         "t_entry_to_stop": 1, "t_stop_to_mfe": 4, "target_after_stop": False,
         "mae_before_recovery_R": 1.2, "recovered": True, "t_stop_to_recovery": 3},
        {"symbol": "MES", "execution": "TRADE", "stopped": True, "eventual_mfe_R": 0.5,
         "bucket": "true_invalidation", "mfe_before_stop": 0.5, "mfe_after_stop": 0.5,
         "t_entry_to_stop": 2, "t_stop_to_mfe": 0, "target_after_stop": False,
         "mae_before_recovery_R": None, "recovered": False, "t_stop_to_recovery": None},
        {"symbol": "MNQ", "execution": "PASS", "stopped": False, "eventual_mfe_R": 5.0,
         "bucket": "survived", "mfe_before_stop": 5.0},
    ]
    a = SP.analyze(rows)
    assert a["n_stopped"] == 2 and a["n_survived"] == 1
    assert a["buckets_all_stopped"]["premature_stop"] == 1
    assert a["buckets_meaningful"]["premature_stop"] == 1     # the 3R one qualifies as meaningful
    assert a["buckets_meaningful"]["true_invalidation"] == 0  # the 0.5R one is not "meaningful"
    assert a["premature_within_1.5R"] == 1.0                  # 1.2R <= 1.5R
