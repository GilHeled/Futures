"""Stall-characterization: per-trade geometric measures are self-consistent (the entry/stop/
displacement identity holds), and the aggregation buckets/correlations compute correctly."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.engine import pipeline
from ict_live.journal import stall_analysis as SA
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


def _series(n, seed):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(minutes=15 * i)
        bars.append(Bar("15m", ot, ot + timedelta(minutes=15), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_measure_geometric_identity():
    for seed in range(1, 300):
        sig = _series(300, seed=seed)
        ms = pipeline.analyze(sig[:240], "15m")
        if ms.recommendation.setup is None:
            continue
        m = SA.measure(ms, sig, 239)
        if m is None:
            continue
        assert m["mfe_R"] >= 0
        assert m["disp_size_R"] > 0
        # entry sits inside the displacement leg
        assert 0.0 < m["entry_frac_f"] < 1.5
        # identity: when the stop equals the displacement start, the distance from entry back to
        # the impulse high is exactly (disp_size_R - 1) R
        if m["stop_is_disp_start"]:
            assert abs(m["dist_entry_to_dispEnd_R"] - (m["disp_size_R"] - 1)) < 0.05
        return
    raise AssertionError("no setup produced by the synthetic series")


def test_atr_and_fill_helpers():
    t0 = datetime(2026, 6, 2, 9, tzinfo=ET)
    bars = [Bar("1H", t0 + timedelta(hours=i), t0 + timedelta(hours=i + 1),
                100 + i, 101 + i, 99 + i, 100 + i, 1.0) for i in range(20)]
    assert SA._atr(bars, 15, period=14) is not None
    assert SA._fill_index(bars, 5, bars[7].low, 10) is not None      # entry inside a later bar
    assert SA._fill_index(bars, 5, 9999, 10) is None                 # never touched


def test_analyze_buckets_and_pivot():
    rows = [
        {"mfe_R": 2.0, "disp_size_R": 3.0, "entry_frac_f": 0.33, "dist_entry_to_dispEnd_R": 2.0,
         "mfe_vs_dispEnd": 1.0, "dist_opp_liq_R": 2.1, "risk_ATR": 0.5, "mfe_ATR": 1.0,
         "stop_is_disp_start": True, "mfe_is_pivot": True, "execution": "TRADE", "symbol": "MES"},
        {"mfe_R": 2.2, "disp_size_R": 3.2, "entry_frac_f": 0.31, "dist_entry_to_dispEnd_R": 2.2,
         "mfe_vs_dispEnd": 1.05, "dist_opp_liq_R": 2.0, "risk_ATR": 0.6, "mfe_ATR": 1.3,
         "stop_is_disp_start": True, "mfe_is_pivot": True, "execution": "TRADE", "symbol": "MNQ"},
        {"mfe_R": 0.5, "disp_size_R": 4.0, "entry_frac_f": 0.25, "dist_entry_to_dispEnd_R": 3.0,
         "mfe_vs_dispEnd": 0.3, "dist_opp_liq_R": 5.0, "risk_ATR": 0.7, "mfe_ATR": 0.35,
         "stop_is_disp_start": True, "mfe_is_pivot": False, "execution": "PASS", "symbol": "MES"},
    ]
    a = SA.analyze(rows)
    assert a["n"] == 3
    assert a["stall_vs_impulse_high"]["at_0.8-1.2"] == 2      # two stalled at the impulse high
    assert a["stall_vs_impulse_high"]["below_0.8"] == 1
    assert a["mfe_is_pivot_pct"] == round(100 * 2 / 3, 0)
    assert a["mfe_R"]["median"] is not None
