from mnq_system.candlesticks import (
    Bar,
    CandleConfig,
    is_bearish_engulfing,
    is_bullish_engulfing,
    is_doji,
    is_exhaustion_wick,
    is_hammer,
    is_shooting_star,
)

CFG = CandleConfig()


def test_is_bullish_engulfing_true_when_green_body_fully_engulfs_prior_red_body():
    prev = Bar(open=10, high=10.2, low=8.8, close=9.0)  # red
    curr = Bar(open=8.9, high=10.5, low=8.8, close=10.1)  # green, engulfs prev body

    assert is_bullish_engulfing(prev, curr) is True


def test_is_bullish_engulfing_false_when_curr_does_not_fully_engulf():
    prev = Bar(open=10, high=10.2, low=8.8, close=9.0)
    curr = Bar(open=9.5, high=10.0, low=9.4, close=9.8)  # green but doesn't engulf

    assert is_bullish_engulfing(prev, curr) is False


def test_is_bullish_engulfing_false_when_prev_bar_was_not_red():
    prev = Bar(open=9.0, high=10.2, low=8.8, close=10.0)  # green, not red
    curr = Bar(open=8.9, high=10.5, low=8.8, close=10.1)

    assert is_bullish_engulfing(prev, curr) is False


def test_is_bearish_engulfing_true_when_red_body_fully_engulfs_prior_green_body():
    prev = Bar(open=9.0, high=10.2, low=8.8, close=10.0)  # green
    curr = Bar(open=10.1, high=10.2, low=8.5, close=8.9)  # red, engulfs prev body

    assert is_bearish_engulfing(prev, curr) is True


def test_is_hammer_true_for_long_lower_wick_small_body_small_upper_wick():
    bar = Bar(open=10.0, high=10.25, low=8.0, close=10.2)
    assert is_hammer(bar, CFG) is True


def test_is_hammer_false_for_a_normal_bodied_candle():
    bar = Bar(open=10.0, high=10.5, low=9.5, close=10.4)
    assert is_hammer(bar, CFG) is False


def test_is_hammer_false_for_zero_body():
    bar = Bar(open=10.0, high=10.1, low=9.9, close=10.0)
    assert is_hammer(bar, CFG) is False


def test_is_shooting_star_true_for_long_upper_wick_small_body_small_lower_wick():
    bar = Bar(open=10.0, high=12.0, low=9.95, close=9.95)
    assert is_shooting_star(bar, CFG) is True


def test_is_shooting_star_false_when_it_is_actually_a_hammer_shape():
    bar = Bar(open=10.0, high=10.1, low=8.0, close=10.05)
    assert is_shooting_star(bar, CFG) is False


def test_is_doji_true_when_body_is_tiny_relative_to_range():
    bar = Bar(open=10.0, high=10.5, low=9.5, close=10.02)
    assert is_doji(bar, CFG) is True


def test_is_doji_false_for_a_wide_body():
    bar = Bar(open=10.0, high=10.5, low=9.5, close=10.4)
    assert is_doji(bar, CFG) is False


def test_is_doji_false_when_high_equals_low():
    bar = Bar(open=10.0, high=10.0, low=10.0, close=10.0)
    assert is_doji(bar, CFG) is False


def test_is_exhaustion_wick_true_for_upper_wick_against_a_prior_uptrend():
    bar = Bar(open=10.0, high=12.0, low=9.9, close=10.1)
    assert is_exhaustion_wick(bar, prior_trend="up", cfg=CFG) is True


def test_is_exhaustion_wick_false_when_trend_direction_does_not_match_wick_side():
    bar = Bar(open=10.0, high=12.0, low=9.9, close=10.1)
    assert is_exhaustion_wick(bar, prior_trend="down", cfg=CFG) is False


def test_is_exhaustion_wick_false_for_zero_body():
    bar = Bar(open=10.0, high=12.0, low=9.9, close=10.0)
    assert is_exhaustion_wick(bar, prior_trend="up", cfg=CFG) is False
