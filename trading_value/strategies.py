"""
Two generic, parameter-light base strategies (§5), fixed a priori. Each emits,
per RTH bar (causally, from completed bars only):
  entry_dir : +1 long / -1 short / 0 none   (signal at this bar's close)
  exit_long / exit_short : baseline-exit signal for an open position (bar close)
Entries/exits are IDENTICAL across all volatility-source arms; only the stop/target
range differs. Entry execution (next-bar open) and eligibility are handled in the
channel, so these functions carry no execution or vol logic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_state.labels import compute_atr
from trading_value import config as C


def _session_vwap(bars: pd.DataFrame) -> pd.Series:
    tp = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    vol = bars["volume"].astype(float)
    g = bars.groupby("et_date", sort=False)
    cum_pv = (tp * vol).groupby(bars["et_date"].values).cumsum()
    cum_v = vol.groupby(bars["et_date"].values).cumsum().replace(0.0, np.nan)
    return cum_pv / cum_v


def compute_signals(bars: pd.DataFrame, strategy: str) -> pd.DataFrame:
    """`bars` = RTH-filtered, sorted, with columns open/high/low/close/volume/et_date."""
    close = bars["close"]
    out = pd.DataFrame(index=bars.index)

    if strategy == "ema_cross":
        ema_f = close.ewm(span=C.EMA_FAST, adjust=False).mean()
        ema_s = close.ewm(span=C.EMA_SLOW, adjust=False).mean()
        diff = ema_f - ema_s
        sign = np.sign(diff.values)
        prev = np.concatenate([[0.0], sign[:-1]])
        cross_up = (sign > 0) & (prev <= 0)
        cross_dn = (sign < 0) & (prev >= 0)
        out["entry_dir"] = np.where(cross_up, 1, np.where(cross_dn, -1, 0))
        out["exit_long"] = cross_dn            # opposite cross closes a long
        out["exit_short"] = cross_up

    elif strategy == "vwap_fade":
        vwap = _session_vwap(bars)
        atr = compute_atr(bars, C.ATR_PERIOD)
        band = C.VWAP_ATR_MULT * atr
        dev = close - vwap
        long_sig = dev < -band
        short_sig = dev > band
        out["entry_dir"] = np.where(long_sig, 1, np.where(short_sig, -1, 0))
        out["exit_long"] = (dev >= 0).values   # reverted to/through VWAP
        out["exit_short"] = (dev <= 0).values
        out.loc[~np.isfinite(band), "entry_dir"] = 0   # no signal before ATR is defined

    else:
        raise ValueError(f"unknown strategy {strategy!r}")

    out["entry_dir"] = out["entry_dir"].astype(int)
    return out
