"""
Tests for the 3 candidate signal-selection policies (mnq_system.strategies.
model_driven.signal_selectors) -- turning a raw, already-threshold-gated
per-bar direction/strength series into a final non-overlapping calendar.
Each selector is a pure function, tested directly without BacktestEngine.
"""

import numpy as np
import pandas as pd
import pytest

from mnq_system.strategies.model_driven.signal_selectors import (
    peak_confirm_calendar,
    rising_edge_calendar,
    window_max_calendar,
)


def _raw(directions: list, strengths: list = None, horizons: list = None) -> pd.DataFrame:
    n = len(directions)
    idx = pd.date_range("2020-01-01", periods=n, freq="5min", tz="UTC")
    strengths = strengths if strengths is not None else [0.0 if d == 0 or d is None else 0.5 for d in directions]
    horizons = horizons if horizons is not None else [np.nan if d == 0 or d is None else 10 for d in directions]
    direction = pd.Series([np.nan if d is None else d for d in directions], index=idx, dtype=float)
    return pd.DataFrame(
        {"direction": direction, "strength": pd.Series(strengths, index=idx, dtype=float),
         "owning_horizon": pd.Series(horizons, index=idx, dtype=float)}
    )


# ---- rising_edge_calendar ----


def test_rising_edge_fires_only_on_the_first_bar_of_a_streak():
    raw = _raw([0, 0, 1, 1, 1, 0, 0])

    calendar = rising_edge_calendar(raw, debounce_bars=1)

    assert calendar["direction"].tolist() == [0, 0, 1, 0, 0, 0, 0]


def test_rising_edge_fires_on_a_hard_reversal_without_passing_through_zero():
    raw = _raw([1, 1, -1, -1, -1])

    calendar = rising_edge_calendar(raw, debounce_bars=1)

    assert calendar["direction"].tolist() == [1, 0, -1, 0, 0]


def test_rising_edge_respects_debounce_even_if_streak_continues():
    raw = _raw([1] * 20)

    calendar = rising_edge_calendar(raw, debounce_bars=10)

    fired_positions = calendar.index[calendar["direction"] != 0]
    assert len(fired_positions) == 1  # never re-fires -- direction never changes again


def test_rising_edge_treats_nan_as_no_signal_and_resets_streak():
    raw = _raw([1, 1, None, 1])  # gap, then "1" again looks like a fresh rising edge

    calendar = rising_edge_calendar(raw, debounce_bars=1)

    assert calendar["direction"].tolist() == [1, 0, 0, 1]


def test_rising_edge_causality_changing_bars_after_cutoff_leaves_earlier_calendar_unchanged():
    raw_a = _raw([0, 1, 1, 0, -1, -1, 0, 1, 1, 1])
    cutoff = 6

    raw_b = raw_a.copy()
    raw_b.iloc[cutoff:] = raw_b.iloc[cutoff:] * 0 - 1  # wildly different tail

    cal_a = rising_edge_calendar(raw_a, debounce_bars=1)
    cal_b = rising_edge_calendar(raw_b, debounce_bars=1)

    pd.testing.assert_frame_equal(cal_a.iloc[:cutoff], cal_b.iloc[:cutoff])


# ---- peak_confirm_calendar ----


def test_peak_confirm_fires_one_bar_after_the_strength_peak():
    raw = _raw(
        directions=[1, 1, 1, 1, 1],
        strengths=[0.5, 0.6, 0.8, 0.7, 0.6],  # peaks at position 2
    )

    calendar = peak_confirm_calendar(raw, debounce_bars=1)

    assert calendar["direction"].tolist() == [0, 0, 0, 1, 0]  # fires at position 3, one bar after the peak


def test_peak_confirm_never_fires_if_strength_never_declines():
    raw = _raw(directions=[1, 1, 1, 1], strengths=[0.5, 0.6, 0.7, 0.8])

    calendar = peak_confirm_calendar(raw, debounce_bars=1)

    assert (calendar["direction"] == 0).all()


def test_peak_confirm_abandons_an_unconfirmed_streak_on_a_hard_reversal():
    raw = _raw(
        directions=[1, 1, 1, -1, -1, -1, -1],
        strengths=[0.5, 0.6, 0.7, 0.9, 0.95, 0.99, 0.8],  # long streak never confirmed; short streak peaks at pos 5
    )

    calendar = peak_confirm_calendar(raw, debounce_bars=1)

    assert calendar["direction"].tolist() == [0, 0, 0, 0, 0, 0, -1]


def test_peak_confirm_respects_debounce_after_firing():
    raw = _raw(
        directions=[1, 1, 1] + [1] * 10,
        strengths=[0.5, 0.9, 0.6] + [0.6] * 10,  # peak at pos 1, confirmed at pos 2
    )

    calendar = peak_confirm_calendar(raw, debounce_bars=5)

    fired = calendar.index[calendar["direction"] != 0]
    assert len(fired) == 1
    assert calendar["direction"].iloc[2] == 1


def test_peak_confirm_causality_holds_for_any_cutoff():
    raw_a = _raw(
        directions=[1, 1, 1, 1, -1, -1, -1, 1, 1, 1],
        strengths=[0.5, 0.7, 0.9, 0.6, 0.4, 0.8, 0.5, 0.3, 0.6, 0.9],
    )
    cutoff = 5

    raw_b = raw_a.copy()
    raw_b.iloc[cutoff:, raw_b.columns.get_indexer(["direction", "strength"])] = [-1, 0.99]

    cal_a = peak_confirm_calendar(raw_a, debounce_bars=1)
    cal_b = peak_confirm_calendar(raw_b, debounce_bars=1)

    pd.testing.assert_frame_equal(cal_a.iloc[:cutoff], cal_b.iloc[:cutoff])


# ---- window_max_calendar ----


def test_window_max_picks_the_strongest_bar_within_each_window():
    raw = _raw(
        directions=[1, 1, -1, 1, 1, 1, -1, 1],
        strengths=[0.3, 0.9, 0.5, 0.2, 0.4, 0.95, 0.5, 0.1],
    )

    calendar = window_max_calendar(raw, window_bars=4)

    # window 0: bars 0-3, strongest is bar 1 (0.9, direction 1) -> decided at bar 3 (window's last bar)
    assert calendar["direction"].iloc[3] == 1
    # window 1: bars 4-7, strongest is bar 5 (0.95, direction 1) -> decided at bar 7
    assert calendar["direction"].iloc[7] == 1
    assert (calendar["direction"].iloc[[0, 1, 2, 4, 5, 6]] == 0).all()


def test_window_max_produces_no_entry_when_a_window_has_no_qualifying_bar():
    raw = _raw(directions=[0, 0, 0, 0], strengths=[0.1, 0.2, 0.1, 0.2])

    calendar = window_max_calendar(raw, window_bars=4)

    assert (calendar["direction"] == 0).all()


def test_window_max_handles_a_partial_trailing_window_without_reading_past_the_end():
    raw = _raw(directions=[1, 1, -1], strengths=[0.4, 0.9, 0.2])  # only 3 bars, window_bars=4

    calendar = window_max_calendar(raw, window_bars=4)

    assert len(calendar) == 3
    assert calendar["direction"].iloc[2] == 1  # decided at the trailing partial window's own last bar


def test_window_max_causality_holds_for_windows_fully_before_an_aligned_cutoff():
    raw_a = _raw(
        directions=[1, 1, -1, 1, -1, 1, 1, -1],
        strengths=[0.3, 0.9, 0.5, 0.2, 0.4, 0.95, 0.5, 0.1],
    )
    cutoff = 4  # aligned to window_bars=4 -- window 0 (bars 0-3) is fully decided before this point

    raw_b = raw_a.copy()
    raw_b.iloc[cutoff:] = raw_b.iloc[cutoff:] * 0 - 1

    cal_a = window_max_calendar(raw_a, window_bars=4)
    cal_b = window_max_calendar(raw_b, window_bars=4)

    pd.testing.assert_frame_equal(cal_a.iloc[:cutoff], cal_b.iloc[:cutoff])
