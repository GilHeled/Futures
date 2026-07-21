"""
Causal cross-timeframe alignment: given a coarser timeframe's bars and a
finer timeframe's current timestamp, find the position of the last
COMPLETED bar on the coarser timeframe. Generalizes what used to be a
single hardcoded "bias timeframe" computation in the backtest engine so any
strategy declaring any number of named timeframes can reuse it.
"""

from __future__ import annotations

import pandas as pd

_INTERVAL_TIMEDELTA = {
    "1m": pd.Timedelta(minutes=1),
    "5m": pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
}


def interval_timedelta(interval: str) -> pd.Timedelta:
    return _INTERVAL_TIMEDELTA[interval]


def bar_end_index(bars_index: pd.DatetimeIndex, interval: str) -> pd.DatetimeIndex:
    """Timestamp each bar in `bars_index` (labeled by its OPEN time) closes at."""
    return bars_index + _INTERVAL_TIMEDELTA[interval]


def as_of_pos(bar_end: pd.DatetimeIndex, t: pd.Timestamp) -> int:
    """Position of the last bar in `bar_end` that has fully closed at or
    before `t`, or -1 if none have yet -- causal by construction.
    """
    return bar_end.searchsorted(t, side="right") - 1
