import pandas as pd

from mnq_system.strategies.ema_fib_reversal.bias import BEARISH, BULLISH, NEUTRAL, get_bias, precompute_bias_inputs
from mnq_system.strategies.ema_fib_reversal.config import EmaConfig, SwingConfig

EMA_CFG = EmaConfig(fast=9, mid=20, slow=50, slope_lookback=10)
SWING_CFG = SwingConfig(lookback=2)


def _zigzag_bars(n_bars: int, direction: int) -> pd.DataFrame:
    """A steadily trending zigzag: net drift each bar plus a short
    oscillation, so it prints a clean sequence of higher-highs/higher-lows
    (direction=1) or lower-highs/lower-lows (direction=-1) while still
    stacking the 9/20/50 EMAs in trend order.
    """
    idx = pd.date_range("2026-01-01", periods=n_bars, freq="15min", tz="UTC")
    price = 100.0
    closes = []
    for i in range(n_bars):
        price += direction * 0.6 + (0.8 if i % 4 < 2 else -0.8)
        closes.append(price)
    highs = [c + 0.4 for c in closes]
    lows = [c - 0.4 for c in closes]
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes}, index=idx)


def _flat_choppy_bars(n_bars: int) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n_bars, freq="15min", tz="UTC")
    closes = [100.0 + (1.5 if i % 2 == 0 else -1.5) for i in range(n_bars)]
    highs = [c + 0.4 for c in closes]
    lows = [c - 0.4 for c in closes]
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes}, index=idx)


def test_get_bias_is_neutral_before_enough_bars_for_slow_ema():
    # Arrange
    bars = _zigzag_bars(n_bars=30, direction=1)
    inputs = precompute_bias_inputs(bars, EMA_CFG, SWING_CFG)

    # Act
    bias = get_bias(bars, inputs, as_of_pos=20, ema_cfg=EMA_CFG, swing_cfg=SWING_CFG)

    # Assert
    assert bias == NEUTRAL


def test_get_bias_is_bullish_for_a_sustained_uptrend():
    # Arrange
    bars = _zigzag_bars(n_bars=90, direction=1)
    inputs = precompute_bias_inputs(bars, EMA_CFG, SWING_CFG)

    # Act
    bias = get_bias(bars, inputs, as_of_pos=89, ema_cfg=EMA_CFG, swing_cfg=SWING_CFG)

    # Assert
    assert bias == BULLISH


def test_get_bias_is_bearish_for_a_sustained_downtrend():
    # Arrange
    bars = _zigzag_bars(n_bars=90, direction=-1)
    inputs = precompute_bias_inputs(bars, EMA_CFG, SWING_CFG)

    # Act
    bias = get_bias(bars, inputs, as_of_pos=89, ema_cfg=EMA_CFG, swing_cfg=SWING_CFG)

    # Assert
    assert bias == BEARISH


def test_get_bias_is_neutral_when_price_chops_sideways():
    # Arrange
    bars = _flat_choppy_bars(n_bars=90)
    inputs = precompute_bias_inputs(bars, EMA_CFG, SWING_CFG)

    # Act
    bias = get_bias(bars, inputs, as_of_pos=89, ema_cfg=EMA_CFG, swing_cfg=SWING_CFG)

    # Assert
    assert bias == NEUTRAL
