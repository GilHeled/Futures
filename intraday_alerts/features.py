"""
Phase-1a feature generator — the 9 FROZEN features (config.FEATURES),
computed causally (each row uses only that row's bar and prior bars/sessions;
no look-ahead). Session-relative features use RTH bars grouped by ET date;
trailing-session references are shifted to exclude the current session.

Returns a DataFrame aligned to `bars.index` with the 9 feature columns
(NaN where not yet computable, e.g. before 10:00 ET or with < required
history). ATR is passed in (config.ATR_PERIOD on the full 5-min series).
"""
from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from intraday_alerts import config as C


def compute_features(bars: pd.DataFrame, atr: pd.Series) -> pd.DataFrame:
    tz = C.TIMEZONE
    et = bars.index.tz_convert(tz)
    date = pd.Index(et.date)
    tod = et.time
    n = len(bars)
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    vol = bars.get("volume", pd.Series(0.0, index=bars.index)).astype(float)

    in_rth = np.array([dt_time(9, 30) <= t < dt_time(16, 0) for t in tod])
    or_win = np.array([dt_time(9, 30) <= t < dt_time(10, 0) for t in tod])

    df = pd.DataFrame(index=bars.index)
    g_date = pd.Series(date, index=bars.index)

    # --- opening range (09:30–10:00), per date; broadcast forward ---
    or_mask = pd.Series(or_win, index=bars.index)
    or_high = high.where(or_mask).groupby(g_date).transform("max")
    or_low = low.where(or_mask).groupby(g_date).transform("min")
    or_mid = (or_high + or_low) / 2.0
    df["or_position"] = (close - or_mid) / atr
    df["or_width"] = (or_high - or_low) / atr

    # --- session phase: fraction of RTH elapsed (09:30 → 16:00 = 390 min) ---
    minutes_since_open = pd.Series(
        [(t.hour - 9) * 60 + (t.minute - 30) if dt_time(9, 30) <= t else np.nan for t in tod],
        index=bars.index,
    )
    df["session_phase"] = (minutes_since_open / 390.0).clip(0.0, 1.0)

    # --- momentum over last 6 bars, ATR-normalized ---
    df["momentum_6"] = (close - close.shift(C.MOMENTUM_LOOKBACK_BARS)) / atr

    # --- session VWAP deviation (cumulative within RTH day) ---
    tp = (high + low + close) / 3.0
    rth = pd.Series(in_rth, index=bars.index)
    pv = (tp * vol).where(rth)
    cum_pv = pv.groupby(g_date).cumsum()
    cum_v = vol.where(rth).groupby(g_date).cumsum()
    vwap = cum_pv / cum_v.replace(0.0, np.nan)
    df["vwap_dev"] = (close - vwap) / atr

    # --- volatility regime: ATR / median(daily-mean-ATR over trailing 20 sessions) ---
    daily_atr = atr.groupby(g_date).mean()
    ref_atr = daily_atr.rolling(C.VOL_REGIME_LOOKBACK_SESSIONS).median().shift(1)  # prior sessions only
    ref_atr_b = g_date.map(ref_atr)
    df["vol_regime"] = atr / ref_atr_b

    # --- overnight gap & return since open (per date) ---
    rth_open_px = bars["open"].where(rth).groupby(g_date).transform("first")
    prior_close = close.where(rth).groupby(g_date).last()
    prior_close_map = g_date.map(prior_close.shift(1))
    df["overnight_gap"] = (rth_open_px - prior_close_map) / atr
    df["return_since_open"] = (close - rth_open_px) / atr

    # --- participation: bar volume / median(volume at same time-of-day over the
    #     trailing 20 sessions). Per-time-of-day, chronological, prior sessions
    #     only (shift(1)) — computed with an explicit per-tod loop for
    #     unambiguous causal index alignment. ---
    tod_key = pd.Series([t.strftime("%H:%M") for t in tod], index=bars.index)
    participation = pd.Series(np.nan, index=bars.index)
    for key in pd.unique(tod_key):
        m = (tod_key == key).to_numpy()
        s = vol[m]
        ref = s.rolling(C.PARTICIPATION_LOOKBACK_SESSIONS).median().shift(1)
        participation[m] = (s / ref.replace(0.0, np.nan)).to_numpy()
    df["participation"] = participation

    return df[list(C.FEATURES)]
