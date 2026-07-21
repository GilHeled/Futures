import pandas as pd

from mnq_system.regime import bucket_percentile, consecutive_run_length, rolling_percentile


def test_rolling_percentile_is_nan_before_lookback_is_satisfied():
    series = pd.Series([1, 2, 3, 4, 5])

    result = rolling_percentile(series, lookback=3)

    assert result.iloc[:2].isna().all()


def test_rolling_percentile_is_one_when_current_value_is_the_window_max():
    series = pd.Series([1, 2, 3, 4, 5])

    result = rolling_percentile(series, lookback=3)

    assert (result.iloc[2:] == 1.0).all()


def test_rolling_percentile_is_low_when_current_value_is_the_window_min():
    series = pd.Series([5, 4, 3, 2, 1])

    result = rolling_percentile(series, lookback=3)

    # current value is the smallest of its own trailing window each time
    assert result.iloc[2] == 1 / 3


def test_bucket_percentile_low_mid_high():
    assert bucket_percentile(0.1) == "low"
    assert bucket_percentile(0.5) == "mid"
    assert bucket_percentile(0.95) == "high"


def test_bucket_percentile_boundary_value_of_one_is_high_not_out_of_range():
    assert bucket_percentile(1.0) == "high"


def test_bucket_percentile_unknown_for_nan():
    assert bucket_percentile(float("nan")) == "unknown"


def test_consecutive_run_length_counts_runs_and_resets_on_change():
    series = pd.Series(["a", "a", "a", "b", "a"])

    result = consecutive_run_length(series)

    assert result.tolist() == [1, 2, 3, 1, 1]


def test_consecutive_run_length_single_value_series():
    series = pd.Series(["a"])

    result = consecutive_run_length(series)

    assert result.tolist() == [1]
