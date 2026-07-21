from dataclasses import replace

import pandas as pd

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig
from mnq_system.strategies.hypotheses.opening_gap import OpeningGapConfig, OpeningGapStrategy
from mnq_system.strategy_api import MarketSnapshot, TimeframeView

_GAP_UP_SPECS = [
    ("2026-06-01 09:00", 100.0, 100.2, 99.8, 100.0),
    ("2026-06-01 09:05", 100.0, 100.2, 99.8, 100.0),
    ("2026-06-01 09:10", 100.0, 100.2, 99.8, 100.0),
    ("2026-06-01 15:50", 100.0, 100.3, 99.8, 100.0),
    ("2026-06-01 15:55", 100.0, 100.4, 99.9, 100.2),  # last bar before 16:00 -> prior close = 100.2
    ("2026-06-01 16:00", 100.2, 100.3, 100.0, 100.1),
    ("2026-06-01 20:00", 100.1, 100.2, 98.8, 99.0),
    ("2026-06-02 09:00", 99.0, 99.2, 98.8, 99.5),
    ("2026-06-02 09:30", 101.0, 101.5, 100.8, 101.2),  # gap = 101.0 - 100.2 = +0.8
]

_NO_GAP_SPECS = [spec for spec in _GAP_UP_SPECS[:-1]] + [("2026-06-02 09:30", 100.25, 100.4, 100.1, 100.3)]


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), timezone="America/New_York"),
    )


def _make_bars(specs, tz="America/New_York"):
    idx = pd.DatetimeIndex([pd.Timestamp(s[0], tz=tz) for s in specs])
    return pd.DataFrame(
        {
            "open": [s[1] for s in specs], "high": [s[2] for s in specs],
            "low": [s[3] for s in specs], "close": [s[4] for s in specs],
            "volume": [1000] * len(specs),
        },
        index=idx,
    )


def _snapshot(entry_bars, j):
    return MarketSnapshot(timeframes={"entry": TimeframeView(entry_bars, j)}, equity=50_000.0)


def _prepared(cfg, account, entry_bars):
    strategy = OpeningGapStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    return strategy


def test_gap_up_beyond_threshold_fires_long_continuation():
    cfg = OpeningGapConfig(exit=HypothesisExitConfig(atr_period=3), min_gap_atr_mult=0.5)
    account = _account()
    entry_bars = _make_bars(_GAP_UP_SPECS)
    j = len(_GAP_UP_SPECS) - 1

    strategy = _prepared(cfg, account, entry_bars)
    for i in range(j + 1):
        strategy.on_bar(_snapshot(entry_bars, i))
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.context["gap"] > 0
    assert signal.setup_type == "opening_gap"


def test_small_gap_below_threshold_does_not_fire():
    cfg = OpeningGapConfig(exit=HypothesisExitConfig(atr_period=3), min_gap_atr_mult=0.5)
    account = _account()
    entry_bars = _make_bars(_NO_GAP_SPECS)
    j = len(_NO_GAP_SPECS) - 1

    strategy = _prepared(cfg, account, entry_bars)
    for i in range(j + 1):
        strategy.on_bar(_snapshot(entry_bars, i))
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_fires_at_most_once_per_day():
    cfg = OpeningGapConfig(exit=HypothesisExitConfig(atr_period=3), min_gap_atr_mult=0.5)
    account = _account()
    later_bar = ("2026-06-02 09:35", 101.2, 101.6, 101.0, 101.4)
    specs = _GAP_UP_SPECS + [later_bar]
    entry_bars = _make_bars(specs)
    j = len(_GAP_UP_SPECS) - 1

    strategy = _prepared(cfg, account, entry_bars)
    for i in range(j + 1):
        strategy.on_bar(_snapshot(entry_bars, i))
    first_signal = strategy.check_entry(_snapshot(entry_bars, j))
    strategy.on_bar(_snapshot(entry_bars, j + 1))
    second_signal = strategy.check_entry(_snapshot(entry_bars, j + 1))

    assert first_signal is not None
    assert second_signal is None


def test_opening_gap_engine_end_to_end():
    cfg = OpeningGapConfig(exit=HypothesisExitConfig(atr_period=3))
    account = _account()
    exit_bar = ("2026-06-02 09:35", 101.2, 105.0, 101.0, 104.5)  # clears the long's target
    entry_bars = _make_bars(_GAP_UP_SPECS + [exit_bar])

    strategy = OpeningGapStrategy(cfg, account)
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"
    assert result.trades[0].setup_type == "opening_gap"
