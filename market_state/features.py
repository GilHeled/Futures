"""
Concrete feature generator — the 10 LOCKED features from docs/IMPLEMENTATION.md
§2, each within a frozen feature family (§3) and strictly causal (every value uses
only that bar and prior bars/sessions; no look-ahead). All are magnitude/activity
quantities; none encodes the sign of returns.

Returns a DataFrame aligned to the RTH bars (same index as labels.build_label_frame)
with the 10 feature columns, NaN where not yet computable.
"""
from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from market_state import config as C
from market_state.labels import compute_atr

FEATURES = (
    "log_rv_lag6",
    "log_rv_lag24",
    "log_rv_session",
    "log_rv_prev_session",
    "atr_regime",
    "or_width",
    "session_phase",
    "participation",
    "gap_abs",
    "efficiency_ratio",
)

_OR_START = dt_time(9, 30)
_OR_END = dt_time(10, 0)


def compute_features(bars: pd.DataFrame, variance: str = "squared_return") -> pd.DataFrame:
    """`bars` must be annotated (data.annotate_session): has in_rth, et_date.
    `variance` selects the per-bar RV measure for the RV-history features
    (frozen default = squared returns; "garman_klass" for the §12 alt-proxy run)."""
    rth = bars[bars["in_rth"]].copy().sort_index()
    g_date = rth["et_date"]
    grp = rth.groupby("et_date", sort=False)
    close, high, low = rth["close"], rth["high"], rth["low"]
    vol = rth.get("volume", pd.Series(0.0, index=rth.index)).astype(float)

    atr = compute_atr(rth, C.ATR_PERIOD)
    lr = np.log(close) - np.log(grp["close"].shift(1))     # within-session log return
    if variance == "garman_klass":
        hl = np.log(high / low)
        co = np.log(close / rth["open"])
        sq = 0.5 * hl ** 2 - (2.0 * np.log(2.0) - 1.0) * co ** 2
    else:
        sq = lr ** 2

    df = pd.DataFrame(index=rth.index)

    # --- RV history family (HAR-style) ---
    def _trail(series, window):
        col = pd.Series(series.values, index=rth.index, name="_x")
        tmp = rth.assign(_x=col)
        gg = tmp.groupby("et_date", sort=False)["_x"]
        total = None
        for m in range(window):
            s = gg.shift(m)
            total = s if total is None else total + s
        return total

    rv_lag6 = _trail(sq, C.HAR_COMPONENT_BARS[0])
    rv_lag24 = _trail(sq, C.HAR_COMPONENT_BARS[1])
    rv_session = sq.groupby(g_date.values).cumsum()        # session-to-date, inclusive
    daily_sq = pd.Series(sq.values, index=g_date.values).groupby(level=0).sum()
    rv_prev_session = g_date.map(daily_sq.shift(1))

    df["log_rv_lag6"] = np.log(rv_lag6.where(rv_lag6 > 0))
    df["log_rv_lag24"] = np.log(rv_lag24.where(rv_lag24 > 0))
    df["log_rv_session"] = np.log(rv_session.where(rv_session > 0))
    df["log_rv_prev_session"] = np.log(rv_prev_session.where(rv_prev_session > 0))

    # --- Volatility regime & range family ---
    daily_atr = atr.groupby(g_date.values).mean()
    ref_atr = daily_atr.rolling(20).median().shift(1)      # prior sessions only
    df["atr_regime"] = atr / g_date.map(ref_atr)

    tod = rth.index.tz_convert(C.TIMEZONE).time
    or_mask = pd.Series([_OR_START <= t < _OR_END for t in tod], index=rth.index)
    or_high = high.where(or_mask).groupby(g_date.values).transform("max")
    or_low = low.where(or_mask).groupby(g_date.values).transform("min")
    df["or_width"] = (or_high - or_low) / atr

    # --- Time-of-day / seasonality family ---
    minutes = pd.Series(
        [(t.hour - 9) * 60 + (t.minute - 30) if t >= _OR_START else np.nan for t in tod],
        index=rth.index,
    )
    df["session_phase"] = (minutes / 390.0).clip(0.0, 1.0)

    # --- Participation / volume family (median volume at same TOD, prior 20 sessions) ---
    tod_key = pd.Series([t.strftime("%H:%M") for t in tod], index=rth.index)
    participation = pd.Series(np.nan, index=rth.index)
    for key in pd.unique(tod_key):
        m = (tod_key == key).to_numpy()
        s = vol[m]
        ref = s.rolling(20).median().shift(1)
        participation[m] = (s / ref.replace(0.0, np.nan)).to_numpy()
    df["participation"] = participation

    # --- Overnight-gap magnitude family (unsigned) ---
    rth_open = rth["open"].groupby(g_date.values).transform("first")
    prior_close = close.groupby(g_date.values).last()
    df["gap_abs"] = (rth_open - g_date.map(prior_close.shift(1))).abs() / atr

    # --- Persistence / efficiency family (Kaufman ER, direction-free) ---
    dclose = close - grp["close"].shift(1)                 # within-session price change
    net = (close - grp["close"].shift(12)).abs()
    gross = _trail(dclose.abs(), 12)
    df["efficiency_ratio"] = net / gross.replace(0.0, np.nan)

    return df[list(FEATURES)]
