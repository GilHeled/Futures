from dataclasses import replace

import pandas as pd

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig
from mnq_system.strategies.hypotheses.overnight_imbalance import OvernightImbalanceConfig, OvernightImbalanceStrategy
from mnq_system.strategy_api import MarketSnapshot, TimeframeView

_UP = (100.0, 100.5, 99.5, 100.2)  # close > open


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
            "volume": [s[5] for s in specs],
        },
        index=idx,
    )


def _snapshot(entry_bars, j):
    return MarketSnapshot(timeframes={"entry": TimeframeView(entry_bars, j)}, equity=50_000.0)


def _run_through(strategy, entry_bars, upto_idx):
    for j in range(upto_idx + 1):
        strategy.on_bar(_snapshot(entry_bars, j))


def _specs(day3_evening_volume):
    o, h, low, c = _UP
    return [
        ("2026-06-01 16:00", o, h, low, c, 1000),  # -> session_date 2026-06-02 (D0)
        ("2026-06-02 09:30", o, h, low, c, 1000),  # D0 entry
        ("2026-06-02 16:00", o, h, low, c, 1000),  # -> D1
        ("2026-06-03 09:30", o, h, low, c, 1000),  # D1 entry
        ("2026-06-03 16:00", o, h, low, c, 1000),  # -> D2
        ("2026-06-04 09:30", o, h, low, c, 1000),  # D2 entry
        ("2026-06-04 16:00", o, h, low, c, day3_evening_volume),  # -> D3
        ("2026-06-05 09:30", o, h, low, c, 1000),  # D3 entry -- test target
    ]


def test_dominant_overnight_imbalance_fires_long():
    cfg = OvernightImbalanceConfig(
        exit=HypothesisExitConfig(atr_period=3), imbalance_lookback_days=3, min_imbalance_percentile=0.75
    )
    account = _account()
    entry_bars = _make_bars(_specs(day3_evening_volume=5000))  # dominant vs the 3 prior days
    j = len(entry_bars) - 1

    strategy = OvernightImbalanceStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.setup_type == "overnight_imbalance"
    assert signal.context["overnight_imbalance_percentile"] == 1.0


def test_non_dominant_imbalance_does_not_fire():
    cfg = OvernightImbalanceConfig(
        exit=HypothesisExitConfig(atr_period=3), imbalance_lookback_days=3, min_imbalance_percentile=0.75
    )
    account = _account()
    entry_bars = _make_bars(_specs(day3_evening_volume=1000))  # same magnitude as every prior day -> tied percentile
    j = len(entry_bars) - 1

    strategy = OvernightImbalanceStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_fires_at_most_once_per_day():
    cfg = OvernightImbalanceConfig(
        exit=HypothesisExitConfig(atr_period=3), imbalance_lookback_days=3, min_imbalance_percentile=0.75
    )
    account = _account()
    specs = _specs(day3_evening_volume=5000) + [("2026-06-05 09:35", *_UP, 1000)]
    entry_bars = _make_bars(specs)
    j = len(_specs(5000)) - 1

    strategy = OvernightImbalanceStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_through(strategy, entry_bars, j)
    first_signal = strategy.check_entry(_snapshot(entry_bars, j))
    strategy.on_bar(_snapshot(entry_bars, j + 1))
    second_signal = strategy.check_entry(_snapshot(entry_bars, j + 1))

    assert first_signal is not None
    assert second_signal is None


def test_overnight_imbalance_engine_end_to_end():
    cfg = OvernightImbalanceConfig(
        exit=HypothesisExitConfig(atr_period=3), imbalance_lookback_days=3, min_imbalance_percentile=0.75
    )
    account = _account()
    exit_spec = ("2026-06-05 09:35", 100.2, 110.0, 100.0, 109.0, 1000)  # clears the long's target
    entry_bars = _make_bars(_specs(day3_evening_volume=5000) + [exit_spec])

    strategy = OvernightImbalanceStrategy(cfg, account)
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"
    assert result.trades[0].setup_type == "overnight_imbalance"
