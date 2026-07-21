"""
Reversal/pullback candlestick confirmation per
references/candlestick-patterns.md. Deliberately kept to the four
easy-to-code, high-value patterns the skill recommends: engulfing,
hammer/shooting star, doji, exhaustion wick.

Each function takes plain OHLC values (or two bars for engulfing) rather
than a DataFrame row, so they're trivial to unit test with literal numbers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class CandleConfig:
    hammer_body_ratio: float = 0.33
    hammer_wick_ratio: float = 2.0
    doji_body_to_range_max: float = 0.1
    exhaustion_wick_to_body_min: float = 2.0


def is_bullish_engulfing(prev: Bar, curr: Bar) -> bool:
    return (
        prev.close < prev.open
        and curr.close > curr.open
        and curr.close >= prev.open
        and curr.open <= prev.close
    )


def is_bearish_engulfing(prev: Bar, curr: Bar) -> bool:
    return (
        prev.close > prev.open
        and curr.close < curr.open
        and curr.close <= prev.open
        and curr.open >= prev.close
    )


def is_hammer(bar: Bar, cfg: CandleConfig) -> bool:
    body = abs(bar.close - bar.open)
    lower_wick = min(bar.close, bar.open) - bar.low
    upper_wick = bar.high - max(bar.close, bar.open)
    return (
        body > 0
        and lower_wick >= cfg.hammer_wick_ratio * body
        and upper_wick <= body * cfg.hammer_body_ratio
    )


def is_shooting_star(bar: Bar, cfg: CandleConfig) -> bool:
    """Bearish mirror of the hammer: long upper wick, small lower wick."""
    body = abs(bar.close - bar.open)
    lower_wick = min(bar.close, bar.open) - bar.low
    upper_wick = bar.high - max(bar.close, bar.open)
    return (
        body > 0
        and upper_wick >= cfg.hammer_wick_ratio * body
        and lower_wick <= body * cfg.hammer_body_ratio
    )


def is_doji(bar: Bar, cfg: CandleConfig) -> bool:
    full_range = bar.high - bar.low
    if full_range <= 0:
        return False
    body = abs(bar.close - bar.open)
    return (body / full_range) <= cfg.doji_body_to_range_max


def is_exhaustion_wick(bar: Bar, prior_trend: str, cfg: CandleConfig) -> bool:
    """A wick >= `exhaustion_wick_to_body_min` x body, opposite the prior
    trend, closing away from that extreme. `prior_trend` is "up" or "down".
    """
    body = abs(bar.close - bar.open)
    if body <= 0:
        return False
    upper_wick = bar.high - max(bar.close, bar.open)
    lower_wick = min(bar.close, bar.open) - bar.low
    if prior_trend == "up":
        return upper_wick >= cfg.exhaustion_wick_to_body_min * body and bar.close < bar.high
    if prior_trend == "down":
        return lower_wick >= cfg.exhaustion_wick_to_body_min * body and bar.close > bar.low
    return False
