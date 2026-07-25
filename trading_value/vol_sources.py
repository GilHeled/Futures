"""
Volatility sources and the range distance D(t) (§2–§3).

Three sources expose one identical per-bar interface:
  none     : V̂ ≡ 1  (range does not adapt to volatility; constant-% distance)
  naive    : HAR forward-variance forecast
  forecast : frozen v2 model forward-variance forecast

  D_raw_source(t) = close(t) · sqrt( V̂_source(t) )                 (price points)
  D_source(t)     = c_source · D_raw_source(t)

Normalization scalars c_source are estimated on DEVELOPMENT eligible bars only, so
mean(D_source) matches the naive arm's dev mean (c_naive = 1). They are frozen and
reused unchanged on the hold-out. When a fresh estimate is unavailable at a bar
(e.g. forecast after 15:25 ET), the last value is carried forward WITHIN the
session (identical rule for all sources).
"""
from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from trading_value import config as C


def _in_entry_window(index) -> np.ndarray:
    t = index.tz_convert(C.TIMEZONE).time
    lo, hi = dt_time(*C.FORECAST_START), dt_time(*C.FORECAST_LAST)
    return np.array([lo <= x <= hi for x in t])


def build_range_distances(bars: pd.DataFrame, vol_streams: pd.DataFrame,
                          c_source: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """`bars` = RTH bars (open/high/low/close/et_date). `vol_streams` = timestamp→
    {V_forecast, V_har}. If `c_source` is given (frozen dev constants) it is used
    verbatim (hold-out); otherwise it is estimated on the eligible bars here (dev)."""
    close = bars["close"].astype(float)
    date = bars["et_date"].values

    vf = vol_streams["V_forecast"].reindex(bars.index)
    vh = vol_streams["V_har"].reindex(bars.index)
    in_win = _in_entry_window(bars.index)

    # a bar is entry-eligible iff both forecast and HAR are present (pre carry-forward) and in-window
    entry_ok = vf.notna().values & vh.notna().values & in_win

    # carry forward the last available estimate WITHIN the session
    vf_ff = vf.groupby(date).ffill()
    vh_ff = vh.groupby(date).ffill()

    d_raw = pd.DataFrame(index=bars.index)
    d_raw["none"] = close                                    # V̂ ≡ 1
    d_raw["naive"] = close * np.sqrt(vh_ff)
    d_raw["forecast"] = close * np.sqrt(vf_ff)

    if c_source is None:
        mask = entry_ok & np.isfinite(d_raw["naive"].values) & np.isfinite(d_raw["forecast"].values)
        mu_star = float(d_raw["naive"].values[mask].mean())      # naive dev-mean distance
        c_source = {
            "none": mu_star / float(d_raw["none"].values[mask].mean()),
            "naive": 1.0,
            "forecast": mu_star / float(d_raw["forecast"].values[mask].mean()),
        }

    D = pd.DataFrame(index=bars.index)
    for src in C.VOL_SOURCES:
        D[src] = c_source[src] * d_raw[src]
    D["entry_ok"] = entry_ok
    return D, c_source
