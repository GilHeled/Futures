from dataclasses import replace

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.indicators import atr
from mnq_system.strategies import get_strategy_spec
from mnq_system.strategies.benchmarks.config import BenchmarkConfig
from mnq_system.strategies.benchmarks.strategy import NaiveBenchmarkStrategy
from mnq_system.strategy_api import MarketSnapshot, Position, TimeframeView

WARMUP_BARS = [(100.0, 100.2, 99.8, 100.0)] * 4  # 09:40-09:55, warms up ATR(3)


def _fast_config(**overrides):
    overrides.setdefault("atr_period", 3)
    overrides.setdefault("entry_time", (10, 0))
    return replace(BenchmarkConfig(), **overrides)


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(
            trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), flatten_before_close=True,
            timezone="America/New_York",
        ),
    )


def _make_bars(bar_tuples, start="2026-06-01 09:40", freq="5min", tz="America/New_York"):
    idx = pd.date_range(start, periods=len(bar_tuples), freq=freq, tz=tz)
    return pd.DataFrame(
        {
            "open": [b[0] for b in bar_tuples], "high": [b[1] for b in bar_tuples],
            "low": [b[2] for b in bar_tuples], "close": [b[3] for b in bar_tuples],
            "volume": [1000] * len(bar_tuples),
        },
        index=idx,
    )


def _snapshot(entry_bars: pd.DataFrame, j: int) -> MarketSnapshot:
    return MarketSnapshot(timeframes={"entry": TimeframeView(entry_bars, j)}, equity=50_000.0)


def _run_on_bar_through(strategy, entry_bars, upto_idx):
    for j in range(upto_idx + 1):
        strategy.on_bar(_snapshot(entry_bars, j))


def test_benchmark_always_long_enters_long_at_the_configured_entry_time():
    cfg, account = _fast_config(direction="long"), _account()
    entry_bar = (100.0, 100.3, 99.7, 100.1)
    entry_bars = _make_bars(WARMUP_BARS + [entry_bar])
    j = len(WARMUP_BARS)

    strategy = NaiveBenchmarkStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    atr_val = atr(entry_bars, period=cfg.atr_period).iloc[j]
    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(100.1)
    assert signal.stop_price == pytest.approx(100.1 - cfg.stop_atr_mult * atr_val)
    assert signal.targets[0] == pytest.approx(100.1 + cfg.stop_atr_mult * atr_val * cfg.target_r_multiple)


def test_benchmark_always_short_enters_short_at_the_configured_entry_time():
    cfg, account = _fast_config(direction="short"), _account()
    entry_bar = (100.0, 100.3, 99.7, 100.1)
    entry_bars = _make_bars(WARMUP_BARS + [entry_bar])
    j = len(WARMUP_BARS)

    strategy = NaiveBenchmarkStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    atr_val = atr(entry_bars, period=cfg.atr_period).iloc[j]
    assert signal is not None
    assert signal.direction == "short"
    assert signal.stop_price == pytest.approx(100.1 + cfg.stop_atr_mult * atr_val)
    assert signal.targets[0] == pytest.approx(100.1 - cfg.stop_atr_mult * atr_val * cfg.target_r_multiple)


def test_benchmark_random_direction_is_deterministic_given_the_same_seed():
    cfg, account = _fast_config(direction="random", random_seed=7), _account()
    entry_bar = (100.0, 100.3, 99.7, 100.1)
    entry_bars = _make_bars(WARMUP_BARS + [entry_bar])
    j = len(WARMUP_BARS)

    def _entry_signal():
        strategy = NaiveBenchmarkStrategy(cfg, account)
        strategy.precompute_batch({"entry": entry_bars})
        _run_on_bar_through(strategy, entry_bars, j)
        return strategy.check_entry(_snapshot(entry_bars, j))

    first, second = _entry_signal(), _entry_signal()

    assert first.direction == second.direction  # same seed, same day -> same draw


def test_benchmark_enters_at_most_once_per_day():
    cfg, account = _fast_config(direction="long"), _account()
    entry_bar = (100.0, 100.3, 99.7, 100.1)
    later_bar = (100.1, 100.4, 99.9, 100.2)
    entry_bars = _make_bars(WARMUP_BARS + [entry_bar, later_bar])
    j = len(WARMUP_BARS)

    strategy = NaiveBenchmarkStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_on_bar_through(strategy, entry_bars, j)
    first_signal = strategy.check_entry(_snapshot(entry_bars, j))
    strategy.on_bar(_snapshot(entry_bars, j + 1))
    second_signal = strategy.check_entry(_snapshot(entry_bars, j + 1))

    assert first_signal is not None
    assert second_signal is None


def test_benchmark_no_entry_before_the_configured_entry_time():
    cfg, account = _fast_config(direction="long", entry_time=(10, 5)), _account()
    entry_bar = (100.0, 100.3, 99.7, 100.1)  # 10:00, one bar before the 10:05 cutoff
    entry_bars = _make_bars(WARMUP_BARS + [entry_bar])
    j = len(WARMUP_BARS)

    strategy = NaiveBenchmarkStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_benchmark_check_exit_stop_takes_priority_over_target_in_the_same_bar():
    position = Position(direction="long", entry_price=100.5, stop_price=99.5, target_1=102.0, contracts=1, contracts_remaining=1)
    entry_bars = _make_bars([(100.0, 103.0, 99.0, 100.0)])  # low pierces stop AND high pierces target
    strategy = NaiveBenchmarkStrategy(_fast_config(), _account())

    decision = strategy.check_exit(_snapshot(entry_bars, 0), position, session_ending=False)

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(99.5)


def test_benchmark_check_exit_full_target():
    position = Position(direction="long", entry_price=100.5, stop_price=99.5, target_1=102.0, contracts=1, contracts_remaining=1)
    entry_bars = _make_bars([(100.0, 102.5, 100.2, 102.3)])
    strategy = NaiveBenchmarkStrategy(_fast_config(), _account())

    decision = strategy.check_exit(_snapshot(entry_bars, 0), position, session_ending=False)

    assert decision.action == "full_target"
    assert decision.fill_price == pytest.approx(102.0)


def test_benchmark_engine_opens_a_trade_end_to_end():
    cfg, account = _fast_config(direction="long"), _account()
    entry_bar = (100.0, 100.3, 99.7, 100.1)
    exit_bar = (100.1, 110.0, 100.0, 109.0)  # comfortably clears any plausible target
    entry_bars = _make_bars(WARMUP_BARS + [entry_bar, exit_bar])

    strategy = NaiveBenchmarkStrategy(cfg, account)
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"
    assert result.trades[0].setup_type == "benchmark_long"


@pytest.mark.parametrize(
    "direction,strategy_name,registry_name",
    [
        ("long", "benchmark_long", "benchmark_always_long"),
        ("short", "benchmark_short", "benchmark_always_short"),
        ("random", "benchmark_random", "benchmark_random"),
    ],
)
def test_benchmark_name_and_registry_status(direction, strategy_name, registry_name):
    strategy = NaiveBenchmarkStrategy(_fast_config(direction=direction), _account())
    assert strategy.name == strategy_name

    spec = get_strategy_spec(registry_name)
    assert spec.status == "benchmark"
