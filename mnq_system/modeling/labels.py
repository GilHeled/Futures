"""
Supervised-learning labels for mnq_system.modeling: the ATR-normalized
forward return at several horizons, discretized into bins.

Labels are explicitly allowed to look forward from a bar -- that's what
makes them labels rather than features (mnq_system/modeling/features.py
must never do this). Same "look forward from a point in time" operation
mnq_system/backtest/excursion.py's MFE/MAE already performs for realized
trades, just applied to every bar instead of only realized entries.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_HORIZONS = (5, 10, 20, 40)
DEFAULT_BIN_EDGES = (-1.5, -0.5, 0.5, 1.5)  # ATR units; 4 edges -> 5 bins
BIN_LABELS = ("big_down", "small_down", "flat", "small_up", "big_up")


def forward_return_atr(close: pd.Series, atr: pd.Series, horizon: int) -> pd.Series:
    """(close[t+horizon] - close[t]) / atr[t]. NaN for the last `horizon`
    bars of the series (no future data yet -- never imputed) and wherever
    atr is NaN or non-positive.
    """
    if horizon <= 0:
        raise ValueError("horizon must be >= 1")
    future_close = close.shift(-horizon)
    atr_safe = atr.where(atr > 0)
    return (future_close - close) / atr_safe


def bin_forward_return(forward_return: pd.Series, bin_edges: tuple = DEFAULT_BIN_EDGES) -> pd.Series:
    """Discretize into integer bin codes 0..len(bin_edges) (half-open
    [edge_i, edge_{i+1}) intervals, unbounded on both ends) -- NaN input
    stays NaN, never imputed.
    """
    edges = (-np.inf, *bin_edges, np.inf)
    return pd.cut(forward_return, bins=edges, labels=False, right=False).astype("float64")


def build_return_bin_labels(
    bars: pd.DataFrame,
    atr: pd.Series,
    horizons: tuple = DEFAULT_HORIZONS,
    bin_edges: tuple = DEFAULT_BIN_EDGES,
) -> dict:
    """dict[horizon -> pd.Series[bin code]], aligned to `bars.index`."""
    return {h: bin_forward_return(forward_return_atr(bars["close"], atr, h), bin_edges) for h in horizons}
