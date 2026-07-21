"""
Configuration for ORBStrategy: a plain Opening Range Breakout on a single
timeframe. See docs/SPEC.md's verification workflow -- every numeric
default here is a conventional starting hypothesis, not a verified/optimal
value. This strategy exists to test a structurally different entry idea
from EmaFibReversalStrategy (no bias filter, no Fibonacci zones, no
candlestick confirmation) through the same validation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ORBConfig:
    entry_timeframe: str = "5m"
    # All times ET, matching AccountConfig.session's convention.
    or_start: tuple = (9, 30)  # opening-range window start (inclusive)
    or_end: tuple = (10, 0)  # opening-range window end (exclusive) -- also the earliest possible entry bar
    entry_cutoff: tuple = (11, 30)  # no new entries at or after this time
    atr_period: int = 14
    # If the opening range's height exceeds this many ATRs, it's too wide to
    # use as the stop distance (risking far more than a normal ATR-based
    # stop would) -- fall back to an ATR-based stop instead.
    max_range_atr_mult: float = 2.0
    stop_atr_mult: float = 1.5  # ATR-based fallback stop distance, when the range itself is too wide
    target_r_multiple: float = 1.5  # fixed R-multiple target off entry risk


DEFAULT_CONFIG = ORBConfig()
