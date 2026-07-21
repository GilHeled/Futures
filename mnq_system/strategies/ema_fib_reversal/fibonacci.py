"""
Fibonacci retracement/extension zones per references/fibonacci.md.
Ratios are conventional starting points, not verified for MNQ -- see
mnq_system/strategies/ema_fib_reversal/config.py and docs/SPEC.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from mnq_system.strategies.ema_fib_reversal.config import FibConfig


@dataclass(frozen=True)
class FibLevels:
    direction: int  # +1 for bullish impulse (low->high), -1 for bearish (high->low)
    swing_start: float
    swing_end: float
    shallow: float
    golden_low: float
    golden_high: float
    invalidation: float
    ext_target_1: float
    ext_target_2: float


def get_fib_levels(swing_start: float, swing_end: float, cfg: FibConfig) -> FibLevels:
    """swing_start -> swing_end is the impulse leg to retrace/extend.
    Bullish impulse: swing_start=low, swing_end=high. Bearish: high -> low.
    """
    diff = swing_end - swing_start
    direction = 1 if diff > 0 else -1
    return FibLevels(
        direction=direction,
        swing_start=swing_start,
        swing_end=swing_end,
        shallow=swing_end - cfg.shallow * diff,
        golden_low=swing_end - cfg.golden_high * diff,
        golden_high=swing_end - cfg.golden_low * diff,
        invalidation=swing_end - cfg.invalidation * diff,
        ext_target_1=swing_end + (cfg.ext_target_1 - 1.0) * diff,
        ext_target_2=swing_end + (cfg.ext_target_2 - 1.0) * diff,
    )


def in_golden_zone(price: float, levels: FibLevels, tolerance: float = 0.0) -> bool:
    lo, hi = sorted((levels.golden_low, levels.golden_high))
    return (lo - tolerance) <= price <= (hi + tolerance)


def in_shallow_zone(price: float, levels: FibLevels, tolerance: float = 0.0) -> bool:
    lo, hi = sorted((levels.golden_high, levels.shallow))
    return (lo - tolerance) <= price <= (hi + tolerance)


def is_invalidated(price: float, levels: FibLevels) -> bool:
    """True once price has retraced beyond the 78.6-100% zone -- the impulse
    has failed and this setup should be discarded, not chased deeper.
    """
    if levels.direction > 0:
        return price <= levels.invalidation
    return price >= levels.invalidation


def has_ema_confluence(price: float, ema_value: float, atr_value: float, cfg: FibConfig) -> bool:
    if atr_value is None or atr_value != atr_value:  # NaN check without importing pandas
        return False
    return abs(price - ema_value) <= cfg.ema_confluence_atr_mult * atr_value
