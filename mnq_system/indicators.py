"""Core numeric indicators: EMA, ATR, session VWAP. Pure pandas, no lookahead."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("period must be >= 1")
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = bars["high"], bars["low"], bars["close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(span=period, adjust=False, min_periods=period).mean()


def session_vwap(bars: pd.DataFrame, tz: str = "America/New_York") -> pd.Series:
    """Volume-weighted average price, reset at the start of each calendar day
    in `tz`. A simplification of a true CME session boundary (which starts
    ~18:00 ET the prior evening, not midnight) -- close enough for a
    reporting/context feature, not precise enough to be a trading rule on
    its own. NaN wherever cumulative volume is zero (e.g. a bar with no
    trades) or before the day's first bar.
    """
    session_day = bars.index.tz_convert(tz).date
    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    dollar_volume = typical_price * bars["volume"]

    cum_dollar_volume = dollar_volume.groupby(session_day).cumsum()
    cum_volume = bars["volume"].groupby(session_day).cumsum().replace(0, float("nan"))
    return cum_dollar_volume / cum_volume
