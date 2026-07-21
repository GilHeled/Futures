"""
Market-regime context features for trade-log enrichment (see
docs/SPEC.md's verification workflow). These are reporting/diagnostic
features to help find behavioral filters -- not entry/exit rules
themselves, and not verified predictors of anything until analyzed against
real trade outcomes.
"""

from __future__ import annotations

import pandas as pd

from mnq_system.indicators import ema

VOLATILITY_BUCKETS = ("low", "mid", "high")
TREND_PERSISTENCE_BARS_THRESHOLD = 4  # conventional heuristic, not tuned


def ema_slope_pct(series: pd.Series, period: int, lookback: int) -> pd.Series:
    """EMA(period)'s percent change over the trailing `lookback` bars --
    (ema_now - ema_prior) / ema_prior. Causal by construction (diff/shift
    only ever look backward). Positive -> rising trend, negative -> falling.
    """
    trend_ema = ema(series, period)
    return trend_ema.diff(lookback) / trend_ema.shift(lookback)


def rolling_percentile(series: pd.Series, lookback: int) -> pd.Series:
    """Percentile rank (0-1) of each value within the trailing `lookback`
    bars (inclusive), causal by construction -- only ever looks backward.
    """
    return series.rolling(lookback, min_periods=lookback).apply(
        lambda window: window.rank(pct=True).iloc[-1], raw=False
    )


def bucket_percentile(pct: float, buckets: tuple = VOLATILITY_BUCKETS) -> str:
    if pd.isna(pct):
        return "unknown"
    n = len(buckets)
    idx = min(n - 1, int(pct * n))
    return buckets[idx]


def consecutive_run_length(series: pd.Series) -> pd.Series:
    """For each position, how many bars (including this one) the value has
    been unchanged from its immediate predecessors. E.g. [A,A,A,B,A] -> [1,2,3,1,1].
    """
    changed = series.ne(series.shift(1))
    run_id = changed.cumsum()
    return series.groupby(run_id).cumcount() + 1
