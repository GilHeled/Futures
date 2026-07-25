"""Fast unit tests for the stop/target execution mechanics + normalization
(the crafted Phase-0 checks that need no market data)."""
import numpy as np
import pandas as pd

from trading_value import config as C
from trading_value import phase0, vol_sources


def test_stop_active_next_bar():
    assert phase0.check_stop_active_next_bar()


def test_stop_tightens_only():
    assert phase0.check_stop_tightens_only()


def test_tp_fixed_from_entry():
    assert phase0.check_tp_fixed_from_entry()


def test_stop_first():
    assert phase0.check_stop_first()


def test_gap_fill_at_open():
    assert phase0.check_gap_fill_at_open()


def test_entry_next_bar_and_costs():
    assert phase0.check_entry_next_bar_and_costs()


def _synth_ctx():
    idx = pd.date_range("2021-06-01 10:00", periods=4, freq="5min", tz=C.TIMEZONE).tz_convert("UTC")
    bars = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
                         "volume": 1000.0}, index=idx)
    bars["et_date"] = pd.Index(idx.tz_convert(C.TIMEZONE).date)
    streams = pd.DataFrame({"V_forecast": [4e-6, 9e-6, 1e-6, 4e-6],
                            "V_har": [4e-6, 4e-6, 4e-6, 4e-6]}, index=idx)
    return bars, streams


def test_normalization_dev_only_matches_means_and_c_naive_is_one():
    bars, streams = _synth_ctx()
    D, c = vol_sources.build_range_distances(bars, streams)
    mask = D["entry_ok"].values
    assert c["naive"] == 1.0
    means = {s: D[s].values[mask].mean() for s in C.VOL_SOURCES}
    assert max(abs(means[s] - means["naive"]) for s in C.VOL_SOURCES) < 1e-6


def test_frozen_c_source_is_reused_verbatim():
    bars, streams = _synth_ctx()
    _, c = vol_sources.build_range_distances(bars, streams)
    D2, c2 = vol_sources.build_range_distances(bars, streams, c_source=c)
    assert c2 == c


def test_entries_identical_across_sources():
    bars, streams = _synth_ctx()
    D, _ = vol_sources.build_range_distances(bars, streams)
    from trading_value import channel
    sig = pd.DataFrame({"entry_dir": [1, 0, 0, 0], "exit_long": False, "exit_short": False},
                       index=bars.index)
    ok = D["entry_ok"].values
    cals = [channel.entry_calendar(bars, sig, ok) for _ in C.VOL_SOURCES]
    assert cals[0] == cals[1] == cals[2]      # calendar is vol-source-independent
