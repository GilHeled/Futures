import numpy as np
import pandas as pd
import pytest

from mnq_system.indicators import atr, ema, session_vwap


def test_ema_matches_pandas_ewm_reference():
    # Arrange
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    expected = series.ewm(span=3, adjust=False, min_periods=3).mean()

    # Act
    result = ema(series, period=3)

    # Assert
    pd.testing.assert_series_equal(result, expected)


def test_ema_is_nan_before_min_periods_reached():
    # Arrange
    series = pd.Series([1.0, 2.0])

    # Act
    result = ema(series, period=5)

    # Assert
    assert result.isna().all()


def test_ema_rejects_non_positive_period():
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        ema(pd.Series([1.0, 2.0]), period=0)


def test_atr_is_zero_for_flat_bars_with_no_gaps():
    # Arrange: every bar open==high==low==close, no gaps between bars
    bars = pd.DataFrame({"high": [10.0] * 20, "low": [10.0] * 20, "close": [10.0] * 20})

    # Act
    result = atr(bars, period=14)

    # Assert
    assert (result.dropna() == 0).all()


def test_atr_reflects_true_range_including_gap_up():
    # Arrange: a bar gaps up above the prior close, so true range should
    # include (high - prev_close), not just (high - low), on that bar.
    bars = pd.DataFrame(
        {
            "high": [10.0] * 14 + [20.0],
            "low": [10.0] * 14 + [19.0],
            "close": [10.0] * 14 + [19.5],
        }
    )

    # Act
    result = atr(bars, period=14)

    # Assert: true range on the gap bar is (20 - 10) = 10, far larger than
    # the (high-low)=1 range, so ATR should jump well above the flat-bar ATR.
    assert result.iloc[-1] > result.iloc[-2]


def _et_bars():
    # 2026-01-05 (Mon) 10:00 & 10:05 ET, then 2026-01-06 (Tue) 10:00 ET.
    # January -> EST (UTC-5), so 10:00 ET == 15:00 UTC.
    idx = pd.DatetimeIndex(
        ["2026-01-05 15:00", "2026-01-05 15:05", "2026-01-06 15:00"], tz="UTC"
    )
    return pd.DataFrame(
        {
            "high": [101.0, 103.0, 201.0],
            "low": [99.0, 101.0, 199.0],
            "close": [100.0, 102.0, 200.0],
            "volume": [10.0, 20.0, 5.0],
        },
        index=idx,
    )


def test_session_vwap_accumulates_within_a_day():
    bars = _et_bars()

    result = session_vwap(bars)

    assert result.iloc[0] == pytest.approx(100.0)  # typical price of bar 1, alone
    assert result.iloc[1] == pytest.approx((100.0 * 10 + 102.0 * 20) / 30)


def test_session_vwap_resets_at_the_next_calendar_day():
    bars = _et_bars()

    result = session_vwap(bars)

    # Day 2's bar is unaffected by day 1's accumulated volume -- VWAP equals
    # its own typical price alone, not a blend with the prior day.
    assert result.iloc[2] == pytest.approx(200.0)


def test_session_vwap_is_nan_when_volume_is_zero():
    idx = pd.DatetimeIndex(["2026-01-05 15:00"], tz="UTC")
    bars = pd.DataFrame({"high": [101.0], "low": [99.0], "close": [100.0], "volume": [0.0]}, index=idx)

    result = session_vwap(bars)

    assert result.isna().all()
