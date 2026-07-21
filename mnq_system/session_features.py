"""
Day-level session features shared between hypothesis strategies
(mnq_system/strategies/hypotheses/) and the market-state feature matrix
(mnq_system/modeling/features.py) -- computed once, vectorized, over a
full bar history rather than duplicated as separate per-bar state tracking
in more than one place.
"""

from __future__ import annotations

from datetime import time as dt_time, timedelta

import numpy as np
import pandas as pd


def prior_session_close_by_date(bars: pd.DataFrame, timezone: str, session_close_time: tuple = (16, 0)) -> dict:
    """For each calendar date D (ET), the close of the last bar strictly
    before `session_close_time` on date D-1 -- the reference every date's
    "opening gap" is measured against. Keyed by the date the gap belongs
    to (D), not the date the close itself occurred on (D-1).
    """
    et_index = bars.index.tz_convert(timezone)
    times = et_index.time
    dates = et_index.date
    close_time = dt_time(*session_close_time)

    df = pd.DataFrame({"date": dates, "close": bars["close"].to_numpy(), "is_pre_close": times < close_time})
    last_pre_close = df.loc[df["is_pre_close"]].groupby("date")["close"].last()
    return {d + timedelta(days=1): v for d, v in last_pre_close.items()}


def overnight_signed_volume_imbalance_by_date(
    bars: pd.DataFrame, timezone: str, session_close_time: tuple = (16, 0), session_open_time: tuple = (9, 30)
) -> dict:
    """For each calendar date D, sum over every bar between D-1's
    session_close_time and D's session_open_time of
    volume * sign(close - open) -- a crude order-flow proxy (no true
    order-flow/tick data available). Keyed by date D.
    """
    et_index = bars.index.tz_convert(timezone)
    times = et_index.time
    dates = et_index.date

    close_time = dt_time(*session_close_time)
    open_time = dt_time(*session_open_time)
    in_overnight = (times >= close_time) | (times < open_time)
    session_date = np.where(times >= close_time, dates + timedelta(days=1), dates)

    signed_volume = bars["volume"].to_numpy() * np.sign(bars["close"].to_numpy() - bars["open"].to_numpy())
    df = pd.DataFrame({"session_date": session_date, "signed_volume": signed_volume, "in_overnight": in_overnight})
    return df.loc[df["in_overnight"]].groupby("session_date")["signed_volume"].sum().to_dict()
