"""
Behavioral tests for EmaFibReversalStrategy, driven either through a real
BacktestEngine (for end-to-end entry/context checks) or by calling the
strategy's own methods directly (for the finer-grained reversal-state-machine
checks). Uses a deliberately small-period config (EMA 2/3/5, swing
lookback=1) so a short, hand-built fixture is enough to warm up bias/EMA/
swings -- the wiring under test is the same as with the real 9/20/50
defaults, just faster to converge for a compact test.
"""

from dataclasses import replace

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.candlesticks import Bar
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.strategies.ema_fib_reversal.config import EmaConfig, EmaFibReversalConfig, SwingConfig
from mnq_system.strategies.ema_fib_reversal.strategy import EmaFibReversalStrategy, ReversalSetup
from mnq_system.timeframe_alignment import as_of_pos, bar_end_index


def _fast_config():
    strategy_cfg = replace(
        EmaFibReversalConfig(),
        ema=EmaConfig(fast=2, mid=3, slow=5, slope_lookback=2),
        swing=SwingConfig(lookback=1),
    )
    account = replace(
        AccountConfig(),
        session=SessionConfig(
            trading_windows=((0, 0, 23, 59),),
            reduced_size_windows=(),
            flatten_before_close=True,
            timezone="UTC",
        ),
    )
    return strategy_cfg, account


# One explicit (open, high, low, close) tuple per 5-minute bar: 3 flat
# warm-up bars, a clean impulse leg (swing low idx4 @ 97.7 -> swing high
# idx16 @ 148.4), a monotonically-declining pullback with no interior pivot,
# then a hammer at idx21 landing in the fib golden zone with EMA confluence.
_LONG_PULLBACK_BARS = [
    (100.0, 100.3, 99.7, 100.0),
    (100.0, 100.3, 99.7, 100.0),
    (100.0, 100.3, 99.7, 100.0),
    (100.0, 100.3, 97.7, 98.0),
    (98.0, 104.3, 97.7, 104.0),
    (104.0, 108.3, 103.7, 108.0),
    (108.0, 112.3, 107.7, 112.0),
    (112.0, 116.3, 111.7, 116.0),
    (116.0, 120.3, 115.7, 120.0),
    (120.0, 124.3, 119.7, 124.0),
    (124.0, 128.3, 123.7, 128.0),
    (128.0, 132.3, 127.7, 132.0),
    (132.0, 136.3, 131.7, 136.0),
    (136.0, 140.3, 135.7, 140.0),
    (140.0, 144.3, 139.7, 144.0),
    (144.0, 148.3, 143.7, 148.0),
    (148.0, 148.4, 143.9, 142.0),
    (142.0, 142.3, 135.7, 136.0),
    (136.0, 136.3, 129.5, 130.0),
    (130.0, 130.3, 123.5, 124.0),
    (124.0, 124.3, 119.0, 120.0),
    (119.5, 119.85, 116.0, 119.8),  # hammer -> expected long entry
    (119.8, 123.3, 119.0, 123.0),
    (123.0, 127.3, 122.7, 127.0),
    (127.0, 131.3, 126.7, 131.0),
    (131.0, 135.3, 130.7, 135.0),
    (135.0, 139.3, 134.7, 139.0),
    (139.0, 143.3, 138.7, 143.0),
    (143.0, 147.3, 142.7, 147.0),
    (147.0, 151.3, 146.7, 151.0),
    (151.0, 155.3, 150.7, 155.0),
    (155.0, 159.3, 154.7, 159.0),
]

_BIAS_CLOSES = [100, 98, 104, 102, 108, 106, 112, 110, 116, 114]


def _make_bias_bars(closes=_BIAS_CLOSES, start="2026-06-01 09:00"):
    idx = pd.date_range(start, periods=len(closes), freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes, "close": closes,
            "high": [c + 0.3 for c in closes], "low": [c - 0.3 for c in closes],
            "volume": [1000] * len(closes),
        },
        index=idx,
    )


def _make_entry_bars(bar_tuples=_LONG_PULLBACK_BARS, start="2026-06-01 09:00"):
    idx = pd.date_range(start, periods=len(bar_tuples), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [b[0] for b in bar_tuples],
            "high": [b[1] for b in bar_tuples],
            "low": [b[2] for b in bar_tuples],
            "close": [b[3] for b in bar_tuples],
            "volume": [1000] * len(bar_tuples),
        },
        index=idx,
    )


def _run_engine(strategy_cfg, account, bias_bars=None, entry_bars=None, equity=50_000.0):
    bias_bars = bias_bars if bias_bars is not None else _make_bias_bars()
    entry_bars = entry_bars if entry_bars is not None else _make_entry_bars()
    strategy = EmaFibReversalStrategy(strategy_cfg, account)
    engine = BacktestEngine(
        {"bias": bias_bars, "entry": entry_bars}, strategy, account, BacktestSettings(account_equity=equity)
    )
    return engine.run(), entry_bars


def test_engine_opens_and_logs_a_long_pullback_trade_from_a_hammer_confirmation():
    strategy_cfg, account = _fast_config()

    result, entry_bars = _run_engine(strategy_cfg, account)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "long"
    assert trade.setup_type == "pullback"
    assert trade.entry_price == pytest.approx(119.8)
    assert trade.entry_time == entry_bars.index[21]
    assert trade.pnl > 0  # price recovers well past entry before the data ends


def test_engine_takes_no_pullback_trades_when_pullback_entries_disabled():
    # Arrange: same fixture that otherwise produces exactly one pullback trade
    strategy_cfg, account = _fast_config()
    strategy_cfg = replace(strategy_cfg, enable_pullback_entries=False)

    result, _ = _run_engine(strategy_cfg, account)

    assert result.trades == []


def test_engine_populates_diagnostic_context_on_opened_trades():
    strategy_cfg, account = _fast_config()

    result, entry_bars = _run_engine(strategy_cfg, account)

    context = result.trades[0].context
    entry_et = entry_bars.index[21].tz_convert(account.session.timezone)
    assert context["entry_weekday"] == entry_et.strftime("%A")
    assert context["entry_hour_et"] == entry_et.hour
    assert context["bias"] == "bullish"
    assert context["atr"] > 0
    assert context["volatility_regime"] in ("low", "mid", "high", "unknown")
    assert context["trend_regime"] in ("trending", "choppy")


def test_engine_captures_initial_stop_and_targets_before_any_breakeven_mutation():
    strategy_cfg, account = _fast_config()

    result, _ = _run_engine(strategy_cfg, account)

    # Assert: the plan as it existed at entry, regardless of what the trade
    # record's final (possibly breakeven-adjusted) stop looked like at close.
    trade = result.trades[0]
    context = trade.context
    targets = context["initial_targets"]
    assert context["initial_entry_price"] == pytest.approx(trade.entry_price)
    assert context["initial_stop_price"] < context["initial_entry_price"]  # long trade
    assert targets[0] > context["initial_entry_price"]
    assert targets[1] > targets[0]


def test_engine_produces_no_trade_when_bias_never_turns_bullish():
    # Arrange: flat/choppy bias timeframe -> bias stays neutral -> no entries possible
    strategy_cfg, account = _fast_config()
    flat_bias = _make_bias_bars(closes=[100, 101, 100, 101, 100, 101, 100, 101, 100, 101])

    result, _ = _run_engine(strategy_cfg, account, bias_bars=flat_bias)

    assert result.trades == []
    assert result.final_equity == 50_000.0


def _bearish_reversal_bars(broken_level):
    """A bearish-engulfing failed-retest pair at `broken_level`, with the
    confirmation candle closing just fractionally below it (near-zero raw
    stop distance, to exercise the stop floor).
    """
    prev_bar = Bar(open=broken_level - 0.02, high=broken_level + 0.10, low=broken_level - 0.05, close=broken_level + 0.05)
    curr_bar = Bar(open=broken_level + 0.10, high=broken_level + 0.15, low=broken_level - 0.20, close=broken_level - 0.03)
    return prev_bar, curr_bar


def _bullish_reversal_bars(broken_level):
    """Mirror of `_bearish_reversal_bars`: a bullish-engulfing failed-retest
    pair, closing just fractionally above `broken_level`.
    """
    prev_bar = Bar(open=broken_level + 0.02, high=broken_level + 0.05, low=broken_level - 0.10, close=broken_level - 0.05)
    curr_bar = Bar(open=broken_level - 0.10, high=broken_level + 0.20, low=broken_level - 0.15, close=broken_level + 0.03)
    return prev_bar, curr_bar


def _prepared_strategy(strategy_cfg, account, bias_bars, entry_bars):
    strategy = EmaFibReversalStrategy(strategy_cfg, account)
    strategy.precompute_batch({"bias": bias_bars, "entry": entry_bars})
    return strategy


def _bias_pos_at(strategy_cfg, bias_bars, entry_bars, j):
    bar_end = bar_end_index(bias_bars.index, strategy_cfg.bias_timeframe)
    return as_of_pos(bar_end, entry_bars.index[j])


def test_manage_reversal_floors_a_near_zero_stop_distance_to_an_atr_fraction():
    # Arrange: the retest closes just 0.01 below the broken level -- a raw
    # stop distance far tighter than a sane fraction of ATR(14).
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    strategy = _prepared_strategy(strategy_cfg, account, bias_bars, entry_bars)
    j = 25
    atr_val = strategy.entry_atr.iloc[j]
    assert atr_val > 1.0  # sanity: fixture's ATR is well above the raw 0.7pt stop distance below

    broken_level = 135.0
    strategy._pending_reversal = ReversalSetup(direction="bearish", broken_level=broken_level, retest_tolerance=1.0)
    strategy._pending_reversal_since = j - 1
    prev_bar, curr_bar = _bearish_reversal_bars(broken_level)
    bias_pos = _bias_pos_at(strategy_cfg, bias_bars, entry_bars, j)

    # Act
    signal = strategy._manage_reversal(j, bias_pos, "neutral", curr_bar, prev_bar)

    # Assert: stop distance was floored to reversal_min_stop_atr_mult * ATR,
    # not left at the raw ~0.7pt distance implied by broken_level vs. entry.
    assert signal is not None
    raw_stop_distance = abs(curr_bar.close - (broken_level + 2 * strategy.tick_size))
    floored_distance = abs(signal.entry_price - signal.stop_price)
    assert floored_distance > raw_stop_distance
    assert floored_distance == pytest.approx(atr_val * strategy_cfg.exit.reversal_min_stop_atr_mult, rel=1e-6)


def test_manage_reversal_skips_entry_when_bias_agrees_with_break_direction():
    # Arrange: bias is already bearish -- a bearish break here is trend
    # continuation, not a change of character, so it should not fire.
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    strategy = _prepared_strategy(strategy_cfg, account, bias_bars, entry_bars)
    j = 25
    broken_level = 135.0
    pending = ReversalSetup(direction="bearish", broken_level=broken_level, retest_tolerance=1.0)
    strategy._pending_reversal = pending
    strategy._pending_reversal_since = j - 1
    prev_bar, curr_bar = _bearish_reversal_bars(broken_level)
    bias_pos = _bias_pos_at(strategy_cfg, bias_bars, entry_bars, j)

    # Act
    signal = strategy._manage_reversal(j, bias_pos, "bearish", curr_bar, prev_bar)

    # Assert: no trade opened, but the setup keeps watching rather than being discarded
    assert signal is None
    assert strategy._pending_reversal is pending


def test_manage_reversal_fires_when_bias_is_neutral_or_opposed():
    # Arrange: same setup, but bias is neutral -- a genuine candidate reversal
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    strategy = _prepared_strategy(strategy_cfg, account, bias_bars, entry_bars)
    j = 25
    broken_level = 135.0
    strategy._pending_reversal = ReversalSetup(direction="bearish", broken_level=broken_level, retest_tolerance=1.0)
    strategy._pending_reversal_since = j - 1
    prev_bar, curr_bar = _bearish_reversal_bars(broken_level)
    bias_pos = _bias_pos_at(strategy_cfg, bias_bars, entry_bars, j)

    # Act
    signal = strategy._manage_reversal(j, bias_pos, "neutral", curr_bar, prev_bar)

    # Assert
    assert signal is not None
    assert signal.direction == "short"


def test_manage_reversal_filter_blocks_long_vs_bearish_bias_when_enabled():
    # Arrange: a bullish reversal setup, but bias reads bearish -- the
    # empirically weak bucket the filter is meant to exclude.
    strategy_cfg, account = _fast_config()
    strategy_cfg = replace(strategy_cfg, filter_reversal_long_vs_bearish_bias=True)
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    strategy = _prepared_strategy(strategy_cfg, account, bias_bars, entry_bars)
    j = 25
    broken_level = 135.0
    pending = ReversalSetup(direction="bullish", broken_level=broken_level, retest_tolerance=1.0)
    strategy._pending_reversal = pending
    strategy._pending_reversal_since = j - 1
    prev_bar, curr_bar = _bullish_reversal_bars(broken_level)
    bias_pos = _bias_pos_at(strategy_cfg, bias_bars, entry_bars, j)

    # Act
    signal = strategy._manage_reversal(j, bias_pos, "bearish", curr_bar, prev_bar)

    # Assert: no trade opened, but the setup keeps watching rather than being discarded
    assert signal is None
    assert strategy._pending_reversal is pending


def test_manage_reversal_filter_disabled_by_default_still_allows_long_vs_bearish_bias():
    # Arrange: same setup as above, but with the filter left at its default (off)
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    strategy = _prepared_strategy(strategy_cfg, account, bias_bars, entry_bars)
    j = 25
    broken_level = 135.0
    strategy._pending_reversal = ReversalSetup(direction="bullish", broken_level=broken_level, retest_tolerance=1.0)
    strategy._pending_reversal_since = j - 1
    prev_bar, curr_bar = _bullish_reversal_bars(broken_level)
    bias_pos = _bias_pos_at(strategy_cfg, bias_bars, entry_bars, j)

    # Act
    signal = strategy._manage_reversal(j, bias_pos, "bearish", curr_bar, prev_bar)

    # Assert
    assert signal is not None
    assert signal.direction == "long"


def test_manage_reversal_filter_does_not_affect_shorts_vs_bullish_bias():
    # Arrange: the filter is asymmetric by design -- it only targets
    # reversal-longs against bearish bias, not reversal-shorts against bullish bias.
    strategy_cfg, account = _fast_config()
    strategy_cfg = replace(strategy_cfg, filter_reversal_long_vs_bearish_bias=True)
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    strategy = _prepared_strategy(strategy_cfg, account, bias_bars, entry_bars)
    j = 25
    broken_level = 135.0
    strategy._pending_reversal = ReversalSetup(direction="bearish", broken_level=broken_level, retest_tolerance=1.0)
    strategy._pending_reversal_since = j - 1
    prev_bar, curr_bar = _bearish_reversal_bars(broken_level)
    bias_pos = _bias_pos_at(strategy_cfg, bias_bars, entry_bars, j)

    # Act
    signal = strategy._manage_reversal(j, bias_pos, "bullish", curr_bar, prev_bar)

    # Assert
    assert signal is not None
    assert signal.direction == "short"


def test_strategy_name_is_ema_fib_reversal():
    strategy_cfg, account = _fast_config()
    strategy = EmaFibReversalStrategy(strategy_cfg, account)

    assert strategy.name == "ema_fib_reversal"
