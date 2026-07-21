"""
Tests for ModelDrivenStrategy's shared, policy-agnostic wiring:
- `_build_raw_signal_series` extracts each horizon's own direction/percentile
  for every bar and delegates to `combine_horizon_signals` once all horizons
  have a valid prediction (never before).
- `detect_event`/`build_context` are pure lookups against a precomputed
  `_signal_calendar` (built by `_build_raw_signal_series` + a signal
  selector) -- not a per-bar recomputation.

Deliberately bypasses on_precompute's real walk-forward retraining (covered
by mnq_system/modeling's own tests and by the per-policy engine end-to-end
tests) by populating `_direction_by_horizon`/`_percentile_by_horizon`
directly.
"""

from dataclasses import replace
from typing import Optional

import numpy as np
import pandas as pd

from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.strategies.model_driven.base import ModelDrivenConfig, ModelDrivenStrategy
from mnq_system.strategy_api import MarketSnapshot, TimeframeView


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), timezone="America/New_York"),
    )


def _make_bars(n=20, start="2026-06-01 09:00", freq="5min", tz="America/New_York"):
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    return pd.DataFrame(
        {"open": [100.0] * n, "high": [100.5] * n, "low": [99.5] * n, "close": [100.0] * n, "volume": [1000] * n},
        index=idx,
    )


class _RecordingPolicy(ModelDrivenStrategy):
    def __init__(self, cfg, account):
        super().__init__(cfg, account)
        self.calls: list = []

    @property
    def name(self) -> str:
        return "recording_policy"

    def combine_horizon_signals(self, horizon_directions: dict, horizon_confidences: dict) -> Optional[str]:
        self.calls.append((dict(horizon_directions), dict(horizon_confidences)))
        return "long" if horizon_directions.get(10) == 1 else None


def _snapshot(bars, j):
    return MarketSnapshot(timeframes={"entry": TimeframeView(bars, j)}, equity=50_000.0)


def _series(bars, values_by_pos):
    """values_by_pos: dict[pos -> value], everything else NaN."""
    s = pd.Series(np.nan, index=bars.index)
    for pos, v in values_by_pos.items():
        s.iloc[pos] = v
    return s


def test_raw_signal_series_extracts_direction_and_confidence_per_horizon_and_delegates():
    cfg = ModelDrivenConfig(horizons=(10, 20, 40))
    account = _account()
    bars = _make_bars()
    j = 5

    strategy = _RecordingPolicy(cfg, account)
    strategy.bars_entry = bars  # normally set by precompute_batch via the parent class
    strategy._direction_by_horizon = {
        10: _series(bars, {j: 1}), 20: _series(bars, {j: -1}), 40: _series(bars, {j: 0}),
    }
    strategy._percentile_by_horizon = {
        10: _series(bars, {j: 0.9}), 20: _series(bars, {j: 0.8}), 40: _series(bars, {j: 0.7}),
    }

    raw = strategy._build_raw_signal_series()

    assert len(strategy.calls) == 1
    directions, confidences = strategy.calls[0]
    assert directions == {10: 1, 20: -1, 40: 0}
    assert confidences == {10: 0.9, 20: 0.8, 40: 0.7}
    assert raw["direction"].iloc[j] == 1  # "long" from combine_horizon_signals
    assert raw["owning_horizon"].iloc[j] == 10  # highest confidence (0.9)
    assert raw["strength"].iloc[j] == 0.9


def test_raw_signal_series_skips_a_bar_when_any_horizon_lacks_a_prediction_yet():
    cfg = ModelDrivenConfig(horizons=(10, 20, 40))
    account = _account()
    bars = _make_bars()
    j = 2  # deliberately left NaN for horizon 40 below

    strategy = _RecordingPolicy(cfg, account)
    strategy.bars_entry = bars
    strategy._direction_by_horizon = {10: _series(bars, {j: 1}), 20: _series(bars, {j: 1}), 40: _series(bars, {})}
    strategy._percentile_by_horizon = {10: _series(bars, {j: 0.9}), 20: _series(bars, {j: 0.9}), 40: _series(bars, {})}

    raw = strategy._build_raw_signal_series()

    assert strategy.calls == []  # combine_horizon_signals must never be consulted without all 3 horizons available
    assert raw["direction"].isna().iloc[j]


def test_detect_event_and_build_context_are_pure_lookups_against_the_calendar():
    cfg = ModelDrivenConfig(horizons=(10, 20))
    account = _account()
    bars = _make_bars()
    j = 3

    strategy = _RecordingPolicy(cfg, account)
    strategy.bars_entry = bars
    strategy._direction_by_horizon = {10: _series(bars, {j: 1}), 20: _series(bars, {j: -1})}
    strategy._percentile_by_horizon = {10: _series(bars, {j: 0.9}), 20: _series(bars, {j: 0.6})}
    calendar_direction = pd.Series(0.0, index=bars.index)
    calendar_direction.iloc[j] = 1
    calendar_horizon = pd.Series(np.nan, index=bars.index)
    calendar_horizon.iloc[j] = 10
    strategy._signal_calendar = pd.DataFrame({"direction": calendar_direction, "owning_horizon": calendar_horizon})

    result = strategy.detect_event(_snapshot(bars, j))
    context = strategy.build_context(_snapshot(bars, j), atr_val=1.0)

    assert result == "long"
    assert context["owning_horizon"] == 10
    assert context["entry_bar_pos"] == j
    assert context["direction_h10"] == 1 and context["direction_h20"] == -1
    assert context["confidence_pctile_h10"] == 0.9 and context["confidence_pctile_h20"] == 0.6


def test_detect_event_returns_none_when_the_calendar_has_no_entry_at_this_bar():
    cfg = ModelDrivenConfig(horizons=(10,))
    account = _account()
    bars = _make_bars()
    j = 4

    strategy = _RecordingPolicy(cfg, account)
    strategy.bars_entry = bars
    strategy._signal_calendar = pd.DataFrame(
        {"direction": pd.Series(0.0, index=bars.index), "owning_horizon": pd.Series(np.nan, index=bars.index)}
    )

    assert strategy.detect_event(_snapshot(bars, j)) is None
