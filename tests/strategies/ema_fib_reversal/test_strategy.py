import pytest

from mnq_system.candlesticks import Bar
from mnq_system.strategies.ema_fib_reversal.config import EmaFibReversalConfig
from mnq_system.strategies.ema_fib_reversal.fibonacci import get_fib_levels
from mnq_system.strategies.ema_fib_reversal.strategy import (
    Position,
    ReversalSetup,
    check_exit,
    check_pullback_entry,
    check_reversal_entry,
)

CFG = EmaFibReversalConfig()


def _bullish_fib():
    # Impulse low=100 -> high=200: golden zone is [138.2, 150]
    return get_fib_levels(swing_start=100.0, swing_end=200.0, cfg=CFG.fib)


def test_check_pullback_entry_long_fires_on_hammer_in_golden_zone_with_confluence():
    fib = _bullish_fib()
    prev_bar = Bar(open=145, high=146, low=140, close=142)
    curr_bar = Bar(open=142, high=143, low=138.5, close=142.8)  # hammer-ish

    signal = check_pullback_entry(
        bias="bullish", price=142.8, fib_levels=fib, ema_mid_value=143.0, atr_value=5.0,
        prev_bar=prev_bar, curr_bar=curr_bar, cfg=CFG,
    )

    assert signal == "long"


def test_check_pullback_entry_returns_none_when_bias_is_neutral():
    fib = _bullish_fib()
    bar = Bar(open=142, high=143, low=138.5, close=142.8)

    signal = check_pullback_entry(
        bias="neutral", price=142.8, fib_levels=fib, ema_mid_value=143.0, atr_value=5.0,
        prev_bar=bar, curr_bar=bar, cfg=CFG,
    )

    assert signal is None


def test_check_pullback_entry_returns_none_outside_the_fib_zone():
    fib = _bullish_fib()
    prev_bar = Bar(open=196, high=197, low=193, close=194)
    curr_bar = Bar(open=194, high=195, low=192, close=196)  # near the swing high, not retraced

    signal = check_pullback_entry(
        bias="bullish", price=196.0, fib_levels=fib, ema_mid_value=196.0, atr_value=5.0,
        prev_bar=prev_bar, curr_bar=curr_bar, cfg=CFG,
    )

    assert signal is None


def test_check_pullback_entry_returns_none_without_ema_confluence():
    fib = _bullish_fib()
    prev_bar = Bar(open=145, high=146, low=140, close=142)
    curr_bar = Bar(open=142, high=143, low=138.5, close=142.8)

    # ema_mid far from price relative to ATR -> no confluence
    signal = check_pullback_entry(
        bias="bullish", price=142.8, fib_levels=fib, ema_mid_value=100.0, atr_value=1.0,
        prev_bar=prev_bar, curr_bar=curr_bar, cfg=CFG,
    )

    assert signal is None


def test_check_pullback_entry_returns_none_when_invalidated():
    fib = _bullish_fib()
    bar = Bar(open=115, high=116, low=110, close=112)  # deep past 78.6% retracement

    signal = check_pullback_entry(
        bias="bullish", price=112.0, fib_levels=fib, ema_mid_value=112.0, atr_value=20.0,
        prev_bar=bar, curr_bar=bar, cfg=CFG,
    )

    assert signal is None


def test_check_pullback_entry_returns_none_without_confirmation_candle():
    fib = _bullish_fib()
    # In zone, EMA confluence, but a plain indecisive candle -- no engulfing/hammer
    bar = Bar(open=142.0, high=142.5, low=141.8, close=142.1)

    signal = check_pullback_entry(
        bias="bullish", price=142.1, fib_levels=fib, ema_mid_value=142.0, atr_value=5.0,
        prev_bar=bar, curr_bar=bar, cfg=CFG,
    )

    assert signal is None


def test_reversal_setup_check_retest_true_when_price_fails_to_reclaim_broken_level():
    setup = ReversalSetup(direction="bullish", broken_level=100.0, retest_tolerance=0.5)
    bar = Bar(open=100.6, high=100.4, low=99.8, close=100.3)  # dips near level, closes above it

    assert setup.check_retest(bar) is True


def test_reversal_setup_check_retest_false_when_price_is_nowhere_near_the_level():
    setup = ReversalSetup(direction="bullish", broken_level=100.0, retest_tolerance=0.5)
    bar = Bar(open=110.0, high=110.5, low=109.5, close=110.2)

    assert setup.check_retest(bar) is False


def test_check_reversal_entry_returns_none_when_not_retested():
    bar = Bar(open=100.6, high=100.4, low=99.8, close=100.3)
    signal = check_reversal_entry(bos_direction="bullish", retested=False, prev_bar=bar, curr_bar=bar, cfg=CFG)
    assert signal is None


def test_check_reversal_entry_fires_long_on_bullish_bos_with_confirmation():
    prev_bar = Bar(open=100.6, high=100.7, low=99.8, close=99.9)  # red
    curr_bar = Bar(open=99.85, high=101.0, low=99.7, close=100.8)  # green, engulfs

    signal = check_reversal_entry(bos_direction="bullish", retested=True, prev_bar=prev_bar, curr_bar=curr_bar, cfg=CFG)

    assert signal == "long"


def test_check_reversal_entry_disabled_by_config_returns_none():
    from dataclasses import replace

    cfg = replace(CFG, enable_reversal_entries=False)
    prev_bar = Bar(open=100.6, high=100.7, low=99.8, close=99.9)
    curr_bar = Bar(open=99.85, high=101.0, low=99.7, close=100.8)

    signal = check_reversal_entry(bos_direction="bullish", retested=True, prev_bar=prev_bar, curr_bar=curr_bar, cfg=cfg)

    assert signal is None


def _long_position(**overrides):
    defaults = dict(
        direction="long", entry_price=100.0, stop_price=95.0, target_1=110.0, target_2=120.0,
        contracts=10, contracts_remaining=10,
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_check_exit_stop_hit_for_long_position():
    position = _long_position()
    bar = Bar(open=96.0, high=96.5, low=94.5, close=95.5)  # low pierces the stop

    decision = check_exit(position, bar, CFG, opposing_signal=False)

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(95.0)
    assert decision.fraction == 1.0


def test_check_exit_partial_target_moves_stop_to_breakeven():
    position = _long_position()
    bar = Bar(open=108.0, high=111.0, low=107.5, close=110.5)  # pierces target_1

    decision = check_exit(position, bar, CFG, opposing_signal=False)

    assert decision.action == "partial_target"
    assert decision.new_stop == pytest.approx(100.0)
    assert decision.fraction == pytest.approx(CFG.exit.partial_exit_fraction)


def test_check_exit_full_target_after_partial_already_taken():
    position = _long_position(partial_taken=True)
    bar = Bar(open=119.0, high=121.0, low=118.5, close=120.5)  # pierces target_2

    decision = check_exit(position, bar, CFG, opposing_signal=False)

    assert decision.action == "full_target"
    assert decision.fraction == 1.0


def test_check_exit_reversal_flatten_when_opposing_signal_fires():
    position = _long_position()
    bar = Bar(open=101.0, high=102.0, low=100.5, close=101.5)  # no stop/target hit

    decision = check_exit(position, bar, CFG, opposing_signal=True)

    assert decision.action == "reversal_flatten"


def test_check_exit_none_when_nothing_has_happened():
    position = _long_position()
    bar = Bar(open=101.0, high=102.0, low=100.5, close=101.5)

    decision = check_exit(position, bar, CFG, opposing_signal=False)

    assert decision.action == "none"


def test_check_exit_short_position_stop_hit_on_high_piercing_stop():
    position = _long_position(direction="short", entry_price=100.0, stop_price=105.0, target_1=90.0, target_2=80.0)
    bar = Bar(open=104.0, high=106.0, low=103.5, close=105.5)

    decision = check_exit(position, bar, CFG, opposing_signal=False)

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(105.0)
