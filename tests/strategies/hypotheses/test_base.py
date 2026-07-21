"""
Proves HypothesisStrategy's shared entry-sizing/exit wiring works
correctly, via a trivial dummy hypothesis (fixed detect_event) with no
real market logic -- mirrors _DummyBuyAndHoldStrategy's role in
tests/test_backtest_engine.py.
"""

from dataclasses import replace
from typing import Optional

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.indicators import atr
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig, HypothesisStrategy
from mnq_system.strategy_api import MarketSnapshot

WARMUP_BARS = [(100.0, 100.2, 99.8, 100.0)] * 4


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), timezone="America/New_York"),
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


class _DummyEventStrategy(HypothesisStrategy):
    """Fires a fixed direction on a fixed bar index -- no real detection logic."""

    def __init__(self, exit_cfg, account, event_bar_index: int, direction: str = "long"):
        super().__init__(exit_cfg, account, entry_timeframe="5m", warmup_bars=exit_cfg.atr_period)
        self._event_bar_index = event_bar_index
        self._direction = direction

    @property
    def name(self) -> str:
        return "dummy_hypothesis"

    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        if snapshot.timeframes["entry"].pos == self._event_bar_index:
            return self._direction
        return None


def test_hypothesis_base_builds_atr_stop_and_r_multiple_target():
    cfg = HypothesisExitConfig(atr_period=3, stop_atr_mult=1.5, target_r_multiple=2.0)
    account = _account()
    event_bar = (100.0, 100.3, 99.7, 100.1)
    entry_bars = _make_bars(WARMUP_BARS + [event_bar])
    j = len(WARMUP_BARS)

    strategy = _DummyEventStrategy(cfg, account, event_bar_index=j, direction="long")
    strategy.precompute_batch({"entry": entry_bars})
    for i in range(j + 1):
        strategy.on_bar(MarketSnapshot(timeframes={"entry": _view(entry_bars, i)}, equity=50_000.0))
    signal = strategy.check_entry(MarketSnapshot(timeframes={"entry": _view(entry_bars, j)}, equity=50_000.0))

    atr_val = atr(entry_bars, period=cfg.atr_period).iloc[j]
    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(100.1)
    assert signal.stop_price == pytest.approx(100.1 - cfg.stop_atr_mult * atr_val)
    assert signal.targets[0] == pytest.approx(100.1 + cfg.stop_atr_mult * atr_val * cfg.target_r_multiple)
    assert signal.context["atr"] == pytest.approx(atr_val)
    assert signal.setup_type == "dummy_hypothesis"


def test_hypothesis_base_returns_none_before_atr_is_warmed_up():
    cfg = HypothesisExitConfig(atr_period=14)  # deliberately not warmed up by bar 0
    account = _account()
    entry_bars = _make_bars([(100.0, 100.3, 99.7, 100.1)])
    strategy = _DummyEventStrategy(cfg, account, event_bar_index=0, direction="long")
    strategy.precompute_batch({"entry": entry_bars})

    signal = strategy.check_entry(MarketSnapshot(timeframes={"entry": _view(entry_bars, 0)}, equity=50_000.0))

    assert signal is None


def test_hypothesis_base_engine_end_to_end():
    cfg = HypothesisExitConfig(atr_period=3)
    account = _account()
    event_bar = (100.0, 100.3, 99.7, 100.1)
    exit_bar = (100.1, 110.0, 100.0, 109.0)
    entry_bars = _make_bars(WARMUP_BARS + [event_bar, exit_bar])
    j = len(WARMUP_BARS)

    strategy = _DummyEventStrategy(cfg, account, event_bar_index=j, direction="long")
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"
    assert result.trades[0].setup_type == "dummy_hypothesis"


def _view(entry_bars, pos):
    from mnq_system.strategy_api import TimeframeView

    return TimeframeView(entry_bars, pos)
