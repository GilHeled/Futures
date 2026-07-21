"""
Bias determination on the bias timeframe (default 15m) per
references/ema-bias.md: a 9/20/50 EMA stack + slope, confirmed by swing
structure. The bias filter only answers "is the system allowed to look for
longs, shorts, or neither" -- it never generates an entry by itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from mnq_system.indicators import ema
from mnq_system.strategies.ema_fib_reversal.config import EmaConfig, SwingConfig
from mnq_system.swings import compute_swings, confirmed_swing_series, get_swing_structure

BULLISH, BEARISH, NEUTRAL = "bullish", "bearish", "neutral"


@dataclass(frozen=True)
class BiasInputs:
    ema_fast: pd.Series
    ema_mid: pd.Series
    ema_slow: pd.Series
    swings: pd.DataFrame


def precompute_bias_inputs(bars_bias_tf: pd.DataFrame, ema_cfg: EmaConfig, swing_cfg: SwingConfig) -> BiasInputs:
    """EMAs and swing flags depend only on past bars, so it's safe (and much
    faster) to compute them once over the whole series; callers must still
    only *consult* them causally via `as_of_pos` (see `get_bias`).
    """
    return BiasInputs(
        ema_fast=ema(bars_bias_tf["close"], ema_cfg.fast),
        ema_mid=ema(bars_bias_tf["close"], ema_cfg.mid),
        ema_slow=ema(bars_bias_tf["close"], ema_cfg.slow),
        swings=compute_swings(bars_bias_tf, lookback=swing_cfg.lookback),
    )


def get_bias(
    bars_bias_tf: pd.DataFrame,
    inputs: BiasInputs,
    as_of_pos: int,
    ema_cfg: EmaConfig,
    swing_cfg: SwingConfig,
) -> str:
    """Bias as of the bar at positional index `as_of_pos` in `bars_bias_tf`."""
    if as_of_pos < max(ema_cfg.slow, ema_cfg.slope_lookback):
        return NEUTRAL

    price = bars_bias_tf["close"].iloc[as_of_pos]
    ema_fast = inputs.ema_fast.iloc[as_of_pos]
    ema_mid = inputs.ema_mid.iloc[as_of_pos]
    ema_slow = inputs.ema_slow.iloc[as_of_pos]
    ema_slow_prior = inputs.ema_slow.iloc[as_of_pos - ema_cfg.slope_lookback]

    if pd.isna(ema_fast) or pd.isna(ema_mid) or pd.isna(ema_slow) or pd.isna(ema_slow_prior):
        return NEUTRAL

    stack_bullish = ema_fast > ema_mid > ema_slow
    stack_bearish = ema_fast < ema_mid < ema_slow
    slope_up = ema_slow > ema_slow_prior
    slope_down = ema_slow < ema_slow_prior

    highs = confirmed_swing_series(bars_bias_tf, inputs.swings, as_of_pos, swing_cfg.lookback, "high", n=2)
    lows = confirmed_swing_series(bars_bias_tf, inputs.swings, as_of_pos, swing_cfg.lookback, "low", n=2)
    structure = get_swing_structure(highs, lows)

    if price > ema_slow and stack_bullish and slope_up and structure == "HH_HL":
        return BULLISH
    if price < ema_slow and stack_bearish and slope_down and structure == "LH_LL":
        return BEARISH
    return NEUTRAL
