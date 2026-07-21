"""Shared OHLCV resampling so every data provider produces bars the same way."""

from __future__ import annotations

import pandas as pd

_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}

# Bar timestamps label the bar's OPEN time (e.g. a "09:30" 5m bar spans
# 09:30:00-09:34:59.999). All providers must normalize to this convention.
_RULE_MAP = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}


def resample_ohlcv(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Resample 1-minute (or finer) OHLCV bars up to `interval`.

    `bars` must have a tz-aware DatetimeIndex and columns
    open/high/low/close/volume. No-op if already at native resolution.
    """
    if interval not in _RULE_MAP:
        raise ValueError(f"Unsupported interval '{interval}', expected one of {list(_RULE_MAP)}")
    rule = _RULE_MAP[interval]
    out = bars.resample(rule, label="left", closed="left").agg(_AGG)
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out
