import numpy as np
import pandas as pd
import pytest

from mnq_system.modeling.labels import (
    DEFAULT_BIN_EDGES,
    bin_forward_return,
    build_return_bin_labels,
    forward_return_atr,
)


def _bars(closes):
    idx = pd.date_range("2026-06-01 09:00", periods=len(closes), freq="5min", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


def test_forward_return_atr_computes_normalized_change():
    closes = [100.0, 101.0, 102.0, 104.0, 108.0]
    atr = pd.Series([2.0] * len(closes), index=_bars(closes).index)

    result = forward_return_atr(_bars(closes)["close"], atr, horizon=2)

    # position 0: (102-100)/2 = 1.0 ; position 2: (108-102)/2 = 3.0
    assert result.iloc[0] == pytest.approx(1.0)
    assert result.iloc[2] == pytest.approx(3.0)


def test_forward_return_atr_is_nan_for_the_last_horizon_bars():
    closes = [100.0, 101.0, 102.0, 104.0, 108.0]
    atr = pd.Series([2.0] * len(closes), index=_bars(closes).index)

    result = forward_return_atr(_bars(closes)["close"], atr, horizon=2)

    assert pd.isna(result.iloc[-1])
    assert pd.isna(result.iloc[-2])
    assert pd.notna(result.iloc[-3])


def test_forward_return_atr_is_nan_when_atr_is_zero_or_missing():
    closes = [100.0, 101.0, 102.0]
    atr = pd.Series([2.0, 0.0, np.nan], index=_bars(closes).index)

    result = forward_return_atr(_bars(closes)["close"], atr, horizon=1)

    assert pd.notna(result.iloc[0])
    assert pd.isna(result.iloc[1])  # atr == 0


def test_bin_forward_return_assigns_expected_bins():
    # DEFAULT_BIN_EDGES = (-1.5, -0.5, 0.5, 1.5) -> 5 bins:
    # 0: (-inf,-1.5) 1: [-1.5,-0.5) 2: [-0.5,0.5) 3: [0.5,1.5) 4: [1.5,inf)
    values = pd.Series([-3.0, -1.0, 0.0, 1.0, 3.0])

    binned = bin_forward_return(values, DEFAULT_BIN_EDGES)

    assert list(binned) == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_bin_forward_return_preserves_nan():
    values = pd.Series([1.0, np.nan, -2.0])

    binned = bin_forward_return(values)

    assert pd.isna(binned.iloc[1])
    assert pd.notna(binned.iloc[0])
    assert pd.notna(binned.iloc[2])


def test_build_return_bin_labels_returns_one_series_per_horizon():
    closes = [100.0 + i for i in range(50)]
    bars = _bars(closes)
    atr = pd.Series([1.0] * len(closes), index=bars.index)

    labels = build_return_bin_labels(bars, atr, horizons=(5, 10))

    assert set(labels.keys()) == {5, 10}
    assert len(labels[5]) == len(bars)
    assert pd.isna(labels[10].iloc[-1])  # last bar has no 10-bar-ahead future data
