import pytest

from mnq_system.strategies.ema_fib_reversal.config import FibConfig
from mnq_system.strategies.ema_fib_reversal.fibonacci import get_fib_levels, has_ema_confluence, in_golden_zone, in_shallow_zone, is_invalidated

CFG = FibConfig()


def test_get_fib_levels_bullish_impulse_orders_levels_below_the_high():
    # Arrange / Act: impulse low=100 -> high=200
    levels = get_fib_levels(swing_start=100.0, swing_end=200.0, cfg=CFG)

    # Assert
    assert levels.direction == 1
    assert levels.shallow == pytest.approx(200 - 0.382 * 100)
    assert levels.golden_low == pytest.approx(200 - 0.618 * 100)
    assert levels.golden_high == pytest.approx(200 - 0.5 * 100)
    assert levels.invalidation == pytest.approx(200 - 0.786 * 100)


def test_get_fib_levels_bullish_extension_targets_are_above_the_high():
    # Arrange / Act
    levels = get_fib_levels(swing_start=100.0, swing_end=200.0, cfg=CFG)

    # Assert
    assert levels.ext_target_1 > levels.swing_end
    assert levels.ext_target_2 > levels.ext_target_1


def test_get_fib_levels_bearish_extension_targets_are_below_the_low():
    # Arrange: impulse high=200 -> low=100 (bearish)
    levels = get_fib_levels(swing_start=200.0, swing_end=100.0, cfg=CFG)

    # Act / Assert
    assert levels.direction == -1
    assert levels.ext_target_1 < levels.swing_end
    assert levels.ext_target_2 < levels.ext_target_1


def test_in_golden_zone_true_at_the_midpoint_of_the_zone():
    # Arrange: golden zone is 50%-61.8% retracement of a 100-point leg -> [138.2, 150]
    levels = get_fib_levels(swing_start=100.0, swing_end=200.0, cfg=CFG)

    # Act / Assert
    assert in_golden_zone(144.0, levels) is True


def test_in_golden_zone_false_outside_the_zone():
    levels = get_fib_levels(swing_start=100.0, swing_end=200.0, cfg=CFG)
    assert in_golden_zone(199.0, levels) is False


def test_in_shallow_zone_covers_the_38_2_to_50_band():
    # Arrange: shallow=161.8, golden_high(50%)=150 -> zone is [150, 161.8]
    levels = get_fib_levels(swing_start=100.0, swing_end=200.0, cfg=CFG)

    # Act / Assert
    assert in_shallow_zone(155.0, levels) is True
    assert in_shallow_zone(145.0, levels) is False


def test_is_invalidated_true_once_retracement_passes_78_6_percent():
    # Arrange: invalidation level = 200 - 0.786*100 = 121.4
    levels = get_fib_levels(swing_start=100.0, swing_end=200.0, cfg=CFG)

    # Act / Assert
    assert is_invalidated(120.0, levels) is True
    assert is_invalidated(130.0, levels) is False


def test_has_ema_confluence_true_within_atr_multiple():
    assert has_ema_confluence(price=100.0, ema_value=99.0, atr_value=2.0, cfg=CFG) is True


def test_has_ema_confluence_false_beyond_atr_multiple():
    assert has_ema_confluence(price=100.0, ema_value=90.0, atr_value=2.0, cfg=CFG) is False


def test_has_ema_confluence_false_when_atr_is_nan():
    assert has_ema_confluence(price=100.0, ema_value=100.0, atr_value=float("nan"), cfg=CFG) is False
