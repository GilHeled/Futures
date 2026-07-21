from dataclasses import replace

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig
from mnq_system.strategies.hypotheses.liquidity_sweep import LiquiditySweepConfig, LiquiditySweepStrategy
from mnq_system.strategy_api import MarketSnapshot, TimeframeView

# Flat bars either side of a confirmed swing high at 105.0 (index 5), then a
# bar that pierces above it and closes back below -> a sweep-and-reject.
_SWEEP_HIGH_BARS = [
    (100.0, 100.5, 99.5, 100.0),
    (100.0, 101.0, 99.5, 100.5),
    (100.5, 102.0, 100.0, 101.5),
    (101.5, 103.0, 101.0, 102.5),
    (102.5, 104.0, 102.0, 103.5),
    (103.5, 105.0, 103.0, 104.0),  # swing high candidate: high=105.0
    (104.0, 103.5, 102.5, 103.0),
    (103.0, 102.5, 101.5, 102.0),
    (102.0, 101.5, 100.5, 101.0),  # confirms the swing high (2 bars each side, lookback=2)
    (101.0, 105.5, 100.5, 104.0),  # sweeps above 105.0 (high=105.5), closes back below (104.0) -> short
]


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59), ), reduced_size_windows=(), timezone="America/New_York"),
    )


def _make_bars(bar_tuples, start="2026-06-01 09:00", freq="5min", tz="America/New_York"):
    idx = pd.date_range(start, periods=len(bar_tuples), freq=freq, tz=tz)
    return pd.DataFrame(
        {
            "open": [b[0] for b in bar_tuples], "high": [b[1] for b in bar_tuples],
            "low": [b[2] for b in bar_tuples], "close": [b[3] for b in bar_tuples],
            "volume": [1000] * len(bar_tuples),
        },
        index=idx,
    )


def _snapshot(entry_bars, j):
    return MarketSnapshot(timeframes={"entry": TimeframeView(entry_bars, j)}, equity=50_000.0)


def _prepared(cfg, account, entry_bars):
    strategy = LiquiditySweepStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    return strategy


def test_sweep_of_a_confirmed_swing_high_fires_short():
    cfg = LiquiditySweepConfig(exit=HypothesisExitConfig(atr_period=3), swing_lookback=2)
    account = _account()
    entry_bars = _make_bars(_SWEEP_HIGH_BARS)
    j = len(_SWEEP_HIGH_BARS) - 1

    strategy = _prepared(cfg, account, entry_bars)
    for i in range(j + 1):
        strategy.on_bar(_snapshot(entry_bars, i))
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "short"
    assert signal.setup_type == "liquidity_sweep"


def test_no_sweep_no_signal():
    cfg = LiquiditySweepConfig(exit=HypothesisExitConfig(atr_period=3), swing_lookback=2)
    account = _account()
    # Same bars but the last bar stays well inside the range -- no sweep.
    quiet_bars = list(_SWEEP_HIGH_BARS[:-1]) + [(101.0, 101.5, 100.5, 101.0)]
    entry_bars = _make_bars(quiet_bars)
    j = len(quiet_bars) - 1

    strategy = _prepared(cfg, account, entry_bars)
    for i in range(j + 1):
        strategy.on_bar(_snapshot(entry_bars, i))
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_liquidity_sweep_engine_end_to_end():
    cfg = LiquiditySweepConfig(exit=HypothesisExitConfig(atr_period=3))
    account = _account()
    exit_bar = (104.0, 104.5, 95.0, 96.0)  # comfortably clears the short's target
    entry_bars = _make_bars(_SWEEP_HIGH_BARS + [exit_bar])

    strategy = LiquiditySweepStrategy(cfg, account)
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) == 1
    assert result.trades[0].direction == "short"
    assert result.trades[0].setup_type == "liquidity_sweep"


def test_liquidity_sweep_decisions_up_to_entry_bar_are_unaffected_by_later_bars():
    cfg = LiquiditySweepConfig(exit=HypothesisExitConfig(atr_period=3))
    account = _account()
    tail_a = [(104.0, 104.5, 103.5, 104.0)]
    tail_b = [(104.0, 500.0, 1.0, 250.0)]
    entry_bars_a = _make_bars(_SWEEP_HIGH_BARS + tail_a)
    entry_bars_b = _make_bars(_SWEEP_HIGH_BARS + tail_b)

    engine_a = BacktestEngine(
        {"entry": entry_bars_a}, LiquiditySweepStrategy(cfg, account), account, BacktestSettings(account_equity=50_000.0)
    )
    engine_b = BacktestEngine(
        {"entry": entry_bars_b}, LiquiditySweepStrategy(cfg, account), account, BacktestSettings(account_equity=50_000.0)
    )
    result_a, result_b = engine_a.run(), engine_b.run()

    assert result_a.trades[0].entry_time == result_b.trades[0].entry_time
    assert result_a.trades[0].direction == result_b.trades[0].direction
