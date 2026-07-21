"""
Unit tests for ORBStrategy. Uses a small ATR period (3, vs. the 14 default)
so a compact fixture (a handful of warmup bars + the 6-bar opening range)
is enough to warm up ATR -- the wiring under test is the same as with the
real default, just faster to converge for a compact test.
"""

from dataclasses import replace

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.indicators import atr
from mnq_system.strategies.orb.config import ORBConfig
from mnq_system.strategies.orb.strategy import ORBStrategy
from mnq_system.strategy_api import MarketSnapshot, Position, TimeframeView

WARMUP_BARS = [
    (100.0, 100.2, 99.8, 100.0),
    (100.0, 100.2, 99.8, 100.0),
    (100.0, 100.2, 99.8, 100.0),
    (100.0, 100.2, 99.8, 100.0),
    (100.0, 100.2, 99.8, 100.0),
    (100.0, 100.2, 99.8, 100.0),
]

# Opening-range bars (09:30-09:55): or_high=100.5, or_low=99.5 -> range=1.0
OR_NARROW_BARS = [
    (100.0, 100.3, 99.8, 100.1),
    (100.1, 100.5, 99.7, 100.2),
    (100.2, 100.4, 99.5, 99.9),
    (99.9, 100.2, 99.8, 100.0),
    (100.0, 100.3, 99.9, 100.1),
    (100.1, 100.2, 99.9, 100.0),
]

# A genuinely choppy 30-minute range (each bar's own true range ~5-15
# points, not a single outlier bar) -> or_high=110.0, or_low=85.0, range=25.
# Built (and checked against mnq_system.indicators.atr directly) so the
# range comfortably exceeds 2x ATR(3) at the breakout bar without the
# breakout bar itself needing an extreme true range.
OR_WIDE_BARS = [
    (100.0, 104.0, 97.0, 101.0),
    (101.0, 110.0, 96.0, 103.0),
    (103.0, 106.0, 85.0, 95.0),
    (95.0, 100.0, 93.0, 97.0),
    (97.0, 102.0, 94.0, 99.0),
    (99.0, 103.0, 95.0, 100.0),
]


def _fast_config(**overrides):
    overrides.setdefault("atr_period", 3)
    return replace(ORBConfig(), **overrides)


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(
            trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), flatten_before_close=True,
            timezone="America/New_York",
        ),
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


def _snapshot(entry_bars: pd.DataFrame, j: int) -> MarketSnapshot:
    return MarketSnapshot(timeframes={"entry": TimeframeView(entry_bars, j)}, equity=50_000.0)


def _run_on_bar_through(strategy, entry_bars, upto_idx):
    for j in range(upto_idx + 1):
        strategy.on_bar(_snapshot(entry_bars, j))


def _prepared_strategy(cfg, account, entry_bars):
    strategy = ORBStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    return strategy


def test_orb_long_breakout_uses_range_opposite_stop_when_range_is_narrow():
    cfg, account = _fast_config(), _account()
    breakout_bar = (100.0, 100.7, 99.9, 100.6)  # high pierces or_high=100.5
    entry_bars = _make_bars(WARMUP_BARS + OR_NARROW_BARS + [breakout_bar])
    j = len(WARMUP_BARS) + len(OR_NARROW_BARS)

    strategy = _prepared_strategy(cfg, account, entry_bars)
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.entry_price == pytest.approx(100.5)
    assert signal.stop_price == pytest.approx(99.5)  # opposite side of the range, not ATR-based
    assert signal.targets[0] == pytest.approx(100.5 + (100.5 - 99.5) * cfg.target_r_multiple)
    assert signal.context["stop_type"] == "range_opposite"
    assert signal.context["range_size_bucket"] in ("narrow", "moderate")


def test_orb_short_breakout_uses_range_opposite_stop_when_range_is_narrow():
    cfg, account = _fast_config(), _account()
    breakout_bar = (100.0, 100.1, 99.3, 99.4)  # low pierces or_low=99.5
    entry_bars = _make_bars(WARMUP_BARS + OR_NARROW_BARS + [breakout_bar])
    j = len(WARMUP_BARS) + len(OR_NARROW_BARS)

    strategy = _prepared_strategy(cfg, account, entry_bars)
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "short"
    assert signal.entry_price == pytest.approx(99.5)
    assert signal.stop_price == pytest.approx(100.5)
    assert signal.targets[0] == pytest.approx(99.5 - (100.5 - 99.5) * cfg.target_r_multiple)


def test_orb_uses_atr_fallback_stop_when_range_is_wide():
    cfg, account = _fast_config(), _account()
    breakout_bar = (100.0, 110.5, 99.5, 101.0)  # breaks the wide or_high=110.0
    entry_bars = _make_bars(WARMUP_BARS + OR_WIDE_BARS + [breakout_bar])
    j = len(WARMUP_BARS) + len(OR_WIDE_BARS)

    # Sanity-check the fixture actually produces a "wide" range before
    # asserting on the branch it's meant to exercise.
    atr_val = atr(entry_bars, period=cfg.atr_period).iloc[j]
    range_size = 110.0 - 85.0
    assert range_size > cfg.max_range_atr_mult * atr_val

    strategy = _prepared_strategy(cfg, account, entry_bars)
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.stop_price == pytest.approx(110.0 - cfg.stop_atr_mult * atr_val)
    assert signal.context["stop_type"] == "atr_fallback"
    assert signal.context["range_size_bucket"] == "wide"


def test_orb_enters_at_most_once_per_day():
    cfg, account = _fast_config(), _account()
    breakout_bar = (100.0, 100.7, 99.9, 100.6)
    second_breakout_bar = (100.6, 110.0, 100.5, 109.0)  # would also break out, later the same day
    entry_bars = _make_bars(WARMUP_BARS + OR_NARROW_BARS + [breakout_bar, second_breakout_bar])
    j = len(WARMUP_BARS) + len(OR_NARROW_BARS)

    strategy = _prepared_strategy(cfg, account, entry_bars)
    _run_on_bar_through(strategy, entry_bars, j)
    first_signal = strategy.check_entry(_snapshot(entry_bars, j))
    strategy.on_bar(_snapshot(entry_bars, j + 1))
    second_signal = strategy.check_entry(_snapshot(entry_bars, j + 1))

    assert first_signal is not None
    assert second_signal is None


def test_orb_no_entry_after_cutoff_time():
    cfg, account = _fast_config(entry_cutoff=(9, 56)), _account()  # cutoff earlier than the breakout bar's time
    breakout_bar = (100.0, 100.7, 99.9, 100.6)
    entry_bars = _make_bars(WARMUP_BARS + OR_NARROW_BARS + [breakout_bar])
    j = len(WARMUP_BARS) + len(OR_NARROW_BARS)
    assert entry_bars.index[j].time() >= pd.Timestamp("2026-06-01 09:56", tz="America/New_York").time()

    strategy = _prepared_strategy(cfg, _account(), entry_bars)
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_orb_no_entry_while_the_range_is_still_forming():
    # A bar inside the 09:30-09:55 window must never itself produce an
    # entry, however extreme its own price action, since the range hasn't
    # closed yet.
    cfg, account = _fast_config(), _account()
    entry_bars = _make_bars(WARMUP_BARS + OR_NARROW_BARS)
    j = len(WARMUP_BARS) + 1  # the second opening-range bar itself

    strategy = _prepared_strategy(cfg, account, entry_bars)
    _run_on_bar_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_orb_check_exit_stop_takes_priority_over_target_in_the_same_bar():
    position = Position(direction="long", entry_price=100.5, stop_price=99.5, target_1=102.0, contracts=1, contracts_remaining=1)
    bar_tuples = [(100.0, 103.0, 99.0, 100.0)]  # low pierces stop AND high pierces target
    entry_bars = _make_bars(bar_tuples)
    strategy = ORBStrategy(_fast_config(), _account())

    decision = strategy.check_exit(_snapshot(entry_bars, 0), position, session_ending=False)

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(99.5)


def test_orb_check_exit_full_target():
    position = Position(direction="long", entry_price=100.5, stop_price=99.5, target_1=102.0, contracts=1, contracts_remaining=1)
    bar_tuples = [(100.0, 102.5, 100.2, 102.3)]  # only the target is pierced
    entry_bars = _make_bars(bar_tuples)
    strategy = ORBStrategy(_fast_config(), _account())

    decision = strategy.check_exit(_snapshot(entry_bars, 0), position, session_ending=False)

    assert decision.action == "full_target"
    assert decision.fill_price == pytest.approx(102.0)


def test_orb_check_exit_none_when_nothing_has_happened():
    position = Position(direction="long", entry_price=100.5, stop_price=99.5, target_1=102.0, contracts=1, contracts_remaining=1)
    bar_tuples = [(100.0, 101.0, 100.2, 100.8)]
    entry_bars = _make_bars(bar_tuples)
    strategy = ORBStrategy(_fast_config(), _account())

    decision = strategy.check_exit(_snapshot(entry_bars, 0), position, session_ending=False)

    assert decision.action == "none"


def test_orb_engine_opens_and_closes_a_trade_end_to_end():
    cfg = _fast_config()
    account = _account()
    breakout_bar = (100.0, 100.7, 99.9, 100.6)
    target_hit_bar = (100.6, 103.0, 100.5, 102.8)  # pierces the 1.5R target
    entry_bars = _make_bars(WARMUP_BARS + OR_NARROW_BARS + [breakout_bar, target_hit_bar])

    strategy = ORBStrategy(cfg, account)
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "long"
    assert trade.setup_type == "orb_breakout"
    assert trade.exit_reason in ("full_target", "session_flatten")


def test_orb_decisions_up_to_the_entry_bar_are_unaffected_by_changing_bars_after_it():
    cfg, account = _fast_config(), _account()
    breakout_bar = (100.0, 100.7, 99.9, 100.6)
    tail_a = [(100.6, 101.0, 100.4, 100.9)]
    tail_b = [(100.6, 500.0, 1.0, 250.0)]  # wildly different future bar
    entry_bars_a = _make_bars(WARMUP_BARS + OR_NARROW_BARS + [breakout_bar] + tail_a)
    entry_bars_b = _make_bars(WARMUP_BARS + OR_NARROW_BARS + [breakout_bar] + tail_b)

    engine_a = BacktestEngine(
        {"entry": entry_bars_a}, ORBStrategy(cfg, account), account, BacktestSettings(account_equity=50_000.0)
    )
    engine_b = BacktestEngine(
        {"entry": entry_bars_b}, ORBStrategy(cfg, account), account, BacktestSettings(account_equity=50_000.0)
    )
    result_a, result_b = engine_a.run(), engine_b.run()

    assert result_a.trades[0].entry_time == result_b.trades[0].entry_time
    assert result_a.trades[0].entry_price == result_b.trades[0].entry_price
    assert result_a.trades[0].direction == result_b.trades[0].direction


def test_strategy_name_is_orb():
    assert ORBStrategy(_fast_config(), _account()).name == "orb"
