from dataclasses import replace

import pandas as pd
import pytest

from mnq_system.config import AccountConfig
from mnq_system.indicators import atr, session_vwap
from mnq_system.modeling.features import DEFAULT_FEATURE_CONFIG, FeatureConfig, build_feature_matrix
from mnq_system.regime import ema_slope_pct

_FAST_CFG = replace(
    DEFAULT_FEATURE_CONFIG, atr_period=3, trend_ema_period=5, trend_slope_lookback=2, swing_lookback=2,
    volatility_lookback_bars=10, overnight_imbalance_lookback_days=3,
)


def _make_bars(closes, opens=None, highs=None, lows=None, volumes=None, start="2026-06-01 09:00", freq="5min"):
    n = len(closes)
    opens = opens or closes
    highs = highs or [c + 0.5 for c in closes]
    lows = lows or [c - 0.5 for c in closes]
    volumes = volumes or [1000] * n
    idx = pd.date_range(start, periods=n, freq=freq, tz="America/New_York")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}, index=idx)


def test_atr_pct_of_price_matches_direct_atr_computation():
    closes = [100.0 + i * 0.3 for i in range(30)]
    bars = _make_bars(closes)

    features = build_feature_matrix({"entry": bars}, AccountConfig(), _FAST_CFG)

    expected = atr(bars, period=_FAST_CFG.atr_period) / bars["close"]
    pd.testing.assert_series_equal(
        features["atr_pct_of_price"], expected.rename("atr_pct_of_price"), check_names=False
    )


def test_vwap_distance_atr_matches_direct_vwap_computation():
    closes = [100.0 + (i % 5) for i in range(30)]
    bars = _make_bars(closes)

    features = build_feature_matrix({"entry": bars}, AccountConfig(), _FAST_CFG)

    vwap = session_vwap(bars, tz="America/New_York")
    atr_series = atr(bars, period=_FAST_CFG.atr_period)
    expected = (bars["close"] - vwap) / atr_series
    pd.testing.assert_series_equal(
        features["vwap_distance_atr"], expected.rename("vwap_distance_atr"), check_names=False
    )


def test_trend_slope_pct_matches_shared_ema_slope_helper():
    closes = [100.0 + i * 1.5 for i in range(30)]
    bars = _make_bars(closes)

    features = build_feature_matrix({"entry": bars}, AccountConfig(), _FAST_CFG)

    expected = ema_slope_pct(bars["close"], _FAST_CFG.trend_ema_period, _FAST_CFG.trend_slope_lookback)
    pd.testing.assert_series_equal(
        features["trend_slope_pct"], expected.rename("trend_slope_pct"), check_names=False
    )


def test_hour_et_and_weekday_reflect_bar_timestamp_in_configured_timezone():
    closes = [100.0] * 10
    bars = _make_bars(closes, start="2026-06-01 09:00")  # a Monday

    features = build_feature_matrix({"entry": bars}, AccountConfig(), _FAST_CFG)

    assert features["hour_et"].iloc[0] == 9
    assert features["weekday"].iloc[0] == 0  # Monday


def test_bars_since_sweep_resets_at_a_sweep_and_grows_afterward():
    # Bars 0-8: flat/quiet, establishing a swing high at bar 5 (confirmed at
    # bar 5+lookback=7). Bar 9 sweeps above it and rejects back below.
    quiet = [
        (100.0, 100.3, 99.8, 100.0), (100.0, 100.3, 99.8, 100.0), (100.0, 100.3, 99.8, 100.0),
        (100.0, 100.3, 99.8, 100.0), (100.0, 100.3, 99.8, 100.0), (100.0, 105.0, 99.8, 104.0),  # swing high @5
        (104.0, 103.5, 102.5, 103.0), (103.0, 102.5, 101.5, 102.0), (102.0, 101.5, 100.5, 101.0),
    ]
    sweep_bar = (101.0, 105.5, 100.5, 104.0)  # pierces 105.0, closes back below
    after_bars = [(104.0, 104.3, 103.8, 104.0)] * 3
    idx = pd.date_range("2026-06-01 09:00", periods=len(quiet) + 1 + len(after_bars), freq="5min", tz="America/New_York")
    tuples = quiet + [sweep_bar] + after_bars
    bars = pd.DataFrame(
        {
            "open": [t[0] for t in tuples], "high": [t[1] for t in tuples],
            "low": [t[2] for t in tuples], "close": [t[3] for t in tuples], "volume": [1000] * len(tuples),
        },
        index=idx,
    )

    features = build_feature_matrix({"entry": bars}, AccountConfig(), _FAST_CFG)
    sweep_idx = len(quiet)

    assert features["bars_since_sweep"].iloc[sweep_idx] == 0
    assert features["bars_since_sweep"].iloc[sweep_idx + 1] == 1
    assert features["bars_since_sweep"].iloc[sweep_idx + 3] == 3
    # Before any sweep has ever occurred, bars_since_sweep must not claim "0
    # bars ago" -- it should be strictly positive from the very first bar.
    assert features["bars_since_sweep"].iloc[0] > 0


def test_gap_atr_ratio_reflects_the_daily_open_vs_prior_close_gap():
    specs = [
        ("2026-06-01 09:00", 100.0, 100.2, 99.8, 100.0, 1000),
        ("2026-06-01 15:55", 100.0, 100.4, 99.9, 100.2, 1000),  # last pre-16:00 bar -> prior close for 06-02
        ("2026-06-02 09:30", 101.0, 101.5, 100.8, 101.2, 1000),  # gap = 101.0 - 100.2 = +0.8
        ("2026-06-02 09:35", 101.2, 101.6, 101.0, 101.4, 1000),  # same day -> same gap, broadcast
    ]
    idx = pd.DatetimeIndex([pd.Timestamp(s[0], tz="America/New_York") for s in specs])
    bars = pd.DataFrame(
        {
            "open": [s[1] for s in specs], "high": [s[2] for s in specs], "low": [s[3] for s in specs],
            "close": [s[4] for s in specs], "volume": [s[5] for s in specs],
        },
        index=idx,
    )

    features = build_feature_matrix({"entry": bars}, AccountConfig(), _FAST_CFG)

    # The raw gap (a per-day constant, +0.8) is broadcast across the day, but
    # normalized by EACH bar's own current ATR -- not frozen at the 09:30 bar.
    atr_series = atr(bars, period=_FAST_CFG.atr_period)
    assert features["gap_atr_ratio"].iloc[2] == pytest.approx(0.8 / atr_series.iloc[2])
    assert features["gap_atr_ratio"].iloc[3] == pytest.approx(0.8 / atr_series.iloc[3])
    assert pd.isna(features["gap_atr_ratio"].iloc[0])  # no prior-day reference for the first day


def test_features_up_to_bar_j_are_unaffected_by_changing_bars_after_j():
    closes_a = [100.0 + i * 0.4 for i in range(30)]
    closes_b = list(closes_a)
    for i in range(20, 30):
        closes_b[i] = closes_b[19] - 50.0 * (i - 19)  # wildly different tail

    features_a = build_feature_matrix({"entry": _make_bars(closes_a)}, AccountConfig(), _FAST_CFG)
    features_b = build_feature_matrix({"entry": _make_bars(closes_b)}, AccountConfig(), _FAST_CFG)

    pd.testing.assert_frame_equal(features_a.iloc[:19], features_b.iloc[:19])
