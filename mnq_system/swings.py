"""
Causal (no-lookahead) swing high/low detection and market-structure helpers.

A bar at position i is flagged as a swing high/low using a centered fractal
window of `lookback` bars on each side (bar i's high/low is the max/min of
the window [i-lookback, i+lookback]). Critically, that flag only becomes
*knowable* once the bars up to i+lookback exist -- i.e. the swing at i is
"confirmed" `lookback` bars after it actually happened. Callers must respect
that offset (see `latest_confirmed_swing`) so a backtest never uses
information that would not yet have been visible at that point in time.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


def compute_swings(bars: pd.DataFrame, lookback: int = 2) -> pd.DataFrame:
    """Return a DataFrame aligned to `bars.index` with bool columns
    'is_swing_high' and 'is_swing_low'. The last `lookback` bars (and first
    `lookback`) are always False -- not enough neighbors to confirm yet.
    """
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    window = 2 * lookback + 1
    window_max = bars["high"].rolling(window=window, center=True).max()
    window_min = bars["low"].rolling(window=window, center=True).min()
    is_swing_high = (bars["high"] == window_max) & window_max.notna()
    is_swing_low = (bars["low"] == window_min) & window_min.notna()
    return pd.DataFrame({"is_swing_high": is_swing_high, "is_swing_low": is_swing_low})


@dataclass(frozen=True)
class ConfirmedSwing:
    index_pos: int
    price: float


def confirmed_swing_pivots(
    bars: pd.DataFrame, swings: pd.DataFrame, as_of_pos: int, lookback: int, kind: str, n: int = 5
) -> list[tuple[int, float]]:
    """Last `n` confirmed swing high/low (position, price) pairs as of
    `as_of_pos`, oldest first. A pivot at position p is confirmed once
    p + lookback <= as_of_pos.
    """
    col = "is_swing_high" if kind == "high" else "is_swing_low"
    price_col = "high" if kind == "high" else "low"
    latest_confirmable_pivot = as_of_pos - lookback
    if latest_confirmable_pivot < 0:
        return []
    candidates = swings[col].iloc[: latest_confirmable_pivot + 1]
    pivot_ts_list = candidates[candidates].index[-n:]
    return [(bars.index.get_loc(ts), float(bars[price_col].loc[ts])) for ts in pivot_ts_list]


def latest_confirmed_swing(
    bars: pd.DataFrame, swings: pd.DataFrame, as_of_pos: int, lookback: int, kind: str
) -> ConfirmedSwing | None:
    """Most recent confirmed swing high/low as of `as_of_pos`, or None."""
    pivots = confirmed_swing_pivots(bars, swings, as_of_pos, lookback, kind, n=1)
    if not pivots:
        return None
    pos, price = pivots[-1]
    return ConfirmedSwing(index_pos=pos, price=price)


def confirmed_swing_series(
    bars: pd.DataFrame, swings: pd.DataFrame, as_of_pos: int, lookback: int, kind: str, n: int = 2
) -> list[float]:
    """Last `n` confirmed swing high/low prices as of `as_of_pos`, oldest first."""
    return [price for _, price in confirmed_swing_pivots(bars, swings, as_of_pos, lookback, kind, n=n)]


def find_impulse_leg(
    bars: pd.DataFrame, swings: pd.DataFrame, as_of_pos: int, lookback: int, bias: str
) -> tuple[float, float] | None:
    """Most recent significant impulse leg *in the direction of `bias`*:
    bullish -> most recent confirmed swing low, then the confirmed swing
    high that followed it (low -> high). Bearish is the mirror image.
    Returns (swing_start, swing_end) or None if no such leg is confirmed yet.
    """
    if bias == "bullish":
        lows = confirmed_swing_pivots(bars, swings, as_of_pos, lookback, "low", n=3)
        highs = confirmed_swing_pivots(bars, swings, as_of_pos, lookback, "high", n=3)
        if not lows:
            return None
        low_pos, low_price = lows[-1]
        after = [h for h in highs if h[0] > low_pos]
        if not after:
            return None
        _, high_price = after[-1]
        return (low_price, high_price)
    if bias == "bearish":
        highs = confirmed_swing_pivots(bars, swings, as_of_pos, lookback, "high", n=3)
        lows = confirmed_swing_pivots(bars, swings, as_of_pos, lookback, "low", n=3)
        if not highs:
            return None
        high_pos, high_price = highs[-1]
        after = [l for l in lows if l[0] > high_pos]
        if not after:
            return None
        _, low_price = after[-1]
        return (high_price, low_price)
    return None


def get_swing_structure(recent_highs: list[float], recent_lows: list[float]) -> str:
    """Classify structure from the last two confirmed swing highs/lows in
    chronological order. Returns "HH_HL", "LH_LL", or "mixed".
    """
    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return "mixed"
    higher_high = recent_highs[-1] > recent_highs[-2]
    higher_low = recent_lows[-1] > recent_lows[-2]
    lower_high = recent_highs[-1] < recent_highs[-2]
    lower_low = recent_lows[-1] < recent_lows[-2]
    if higher_high and higher_low:
        return "HH_HL"
    if lower_high and lower_low:
        return "LH_LL"
    return "mixed"


def detect_bos(price: float, last_swing_high: float | None, last_swing_low: float | None) -> str | None:
    """Break of structure: 'bullish' if price breaks above the last
    confirmed swing high, 'bearish' if it breaks below the last confirmed
    swing low, else None. If both would fire (shouldn't happen in practice
    since a swing high > current swing low), bullish takes precedence.
    """
    if last_swing_high is not None and price > last_swing_high:
        return "bullish"
    if last_swing_low is not None and price < last_swing_low:
        return "bearish"
    return None
