"""Synthetic RTH 5-min bars for market_state unit tests (no data fetch)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_state import config as C
from market_state.data import annotate_session


def build_bars(n_days: int = 4, k: float = 0.001, pad: float = 0.0002,
               start="2020-01-06", base: float = 100.0, seed: int | None = None) -> pd.DataFrame:
    """n_days sessions of 78 RTH 5-min bars (09:30–15:55 ET). Within each session
    closes follow c_j = base*exp(k*j) so within-session log returns are constant
    (= k), giving exactly-known realized variance. high/low straddle by `pad`.
    A tz-aware UTC index is returned (as the cache provides)."""
    frames = []
    day = pd.Timestamp(start)
    d = 0
    added = 0
    while added < n_days:
        # weekdays only (mirror a trading calendar loosely)
        if day.weekday() < 5:
            et_idx = pd.date_range(
                f"{day.date()} 09:30", f"{day.date()} 15:55",
                freq="5min", tz=C.TIMEZONE,
            )
            j = np.arange(len(et_idx))
            close = base * np.exp(k * j)
            open_ = np.empty_like(close)
            open_[0] = close[0]
            open_[1:] = close[:-1]
            high = np.maximum(open_, close) * (1.0 + pad)
            low = np.minimum(open_, close) * (1.0 - pad)
            vol = np.full(len(et_idx), 1000.0)
            frames.append(pd.DataFrame(
                {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
                index=et_idx.tz_convert("UTC"),
            ))
            added += 1
        day += pd.Timedelta(days=1)
        d += 1
        if d > 400:
            break
    bars = pd.concat(frames).sort_index()
    return annotate_session(bars)
