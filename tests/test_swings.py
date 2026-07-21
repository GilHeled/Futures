import pandas as pd

from mnq_system.swings import (
    compute_swings,
    confirmed_swing_pivots,
    detect_bos,
    find_impulse_leg,
    get_swing_structure,
    latest_confirmed_swing,
)


def _bars(highs, lows):
    idx = pd.date_range("2026-01-01", periods=len(highs), freq="5min", tz="UTC")
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes}, index=idx)


def test_compute_swings_flags_a_single_clear_peak():
    # Arrange: a clean up-down fractal peak at position 2 with lookback=2
    bars = _bars(highs=[10, 11, 15, 11, 10], lows=[9, 10, 14, 10, 9])

    # Act
    swings = compute_swings(bars, lookback=2)

    # Assert
    assert swings["is_swing_high"].tolist() == [False, False, True, False, False]


def test_compute_swings_does_not_flag_last_lookback_bars_as_unconfirmed():
    # Arrange: a peak sitting in the final `lookback` bars can't be confirmed
    # yet -- there aren't enough future bars to check against.
    bars = _bars(highs=[10, 11, 15], lows=[9, 10, 14])

    # Act
    swings = compute_swings(bars, lookback=2)

    # Assert: nothing is confirmed given only 3 bars and lookback=2
    assert not swings["is_swing_high"].any()


def test_latest_confirmed_swing_ignores_pivots_not_yet_confirmed():
    # Arrange: peak at position 2, lookback=2 -> confirmed only once we've
    # processed bar position 4 (2 + lookback).
    bars = _bars(highs=[10, 11, 15, 11, 10, 9], lows=[9, 10, 14, 10, 9, 8])
    swings = compute_swings(bars, lookback=2)

    # Act
    too_early = latest_confirmed_swing(bars, swings, as_of_pos=3, lookback=2, kind="high")
    confirmed = latest_confirmed_swing(bars, swings, as_of_pos=4, lookback=2, kind="high")

    # Assert
    assert too_early is None
    assert confirmed is not None
    assert confirmed.price == 15
    assert confirmed.index_pos == 2


def test_confirmed_swing_pivots_returns_chronological_order():
    # Arrange: two peaks, at position 2 and position 6
    bars = _bars(
        highs=[10, 11, 15, 11, 10, 11, 16, 11, 10],
        lows=[9, 10, 14, 10, 9, 10, 15, 10, 9],
    )
    swings = compute_swings(bars, lookback=2)

    # Act
    pivots = confirmed_swing_pivots(bars, swings, as_of_pos=8, lookback=2, kind="high", n=5)

    # Assert
    assert [p for p, _ in pivots] == [2, 6]
    assert [round(price, 1) for _, price in pivots] == [15, 16]


def test_get_swing_structure_detects_higher_highs_and_higher_lows():
    assert get_swing_structure(recent_highs=[10, 12], recent_lows=[5, 7]) == "HH_HL"


def test_get_swing_structure_detects_lower_highs_and_lower_lows():
    assert get_swing_structure(recent_highs=[12, 10], recent_lows=[7, 5]) == "LH_LL"


def test_get_swing_structure_is_mixed_when_high_and_low_disagree():
    assert get_swing_structure(recent_highs=[10, 12], recent_lows=[7, 5]) == "mixed"


def test_get_swing_structure_is_mixed_with_fewer_than_two_pivots():
    assert get_swing_structure(recent_highs=[10], recent_lows=[5, 7]) == "mixed"


def test_detect_bos_bullish_when_price_breaks_above_swing_high():
    assert detect_bos(price=101, last_swing_high=100, last_swing_low=90) == "bullish"


def test_detect_bos_bearish_when_price_breaks_below_swing_low():
    assert detect_bos(price=89, last_swing_high=100, last_swing_low=90) == "bearish"


def test_detect_bos_none_when_price_stays_inside_range():
    assert detect_bos(price=95, last_swing_high=100, last_swing_low=90) is None


def test_detect_bos_none_when_no_swings_known_yet():
    assert detect_bos(price=95, last_swing_high=None, last_swing_low=None) is None


def test_find_impulse_leg_bullish_anchors_low_to_the_high_that_followed_it():
    # Arrange: swing low at pos 2, swing high at pos 6 (after the low)
    bars = _bars(
        highs=[20, 19, 12, 13, 14, 18, 22, 18, 17],
        lows=[19, 18, 10, 12, 13, 17, 20, 17, 16],
    )
    swings = compute_swings(bars, lookback=2)

    # Act
    leg = find_impulse_leg(bars, swings, as_of_pos=8, lookback=2, bias="bullish")

    # Assert
    assert leg == (10, 22)


def test_find_impulse_leg_returns_none_when_no_high_followed_the_low_yet():
    # Arrange: only a swing low confirmed so far, no subsequent swing high
    bars = _bars(highs=[20, 19, 12, 13, 14], lows=[19, 18, 10, 12, 13])
    swings = compute_swings(bars, lookback=2)

    # Act
    leg = find_impulse_leg(bars, swings, as_of_pos=4, lookback=2, bias="bullish")

    # Assert
    assert leg is None
