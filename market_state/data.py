"""
Data loading + BOUNDARY ENFORCEMENT for Target A. Two guarantees:

  1. The locked hold-out (2025-01-01 -> 2026-07-09) is inaccessible unless a
     caller explicitly passes allow_holdout=True — so it cannot be touched
     during development by accident. Development uses split="dev" only; the
     single final evaluation is the ONE place allow_holdout=True is permitted.
  2. Only RTH bars are exposed, annotated with the forecast-eligibility window
     (10:00–15:25 ET). Forward-label completeness is enforced downstream in
     labels.py (a forecast is dropped unless its full 6-bar forward window lies
     within the same RTH session).

Reuses the existing CachingProvider (already-paid cached Databento bars); no new
data fetch.
"""
from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from market_state import config as C
from mnq_system.data.providers import build_provider


class HoldoutAccessError(RuntimeError):
    """Raised on any attempt to read the locked hold-out without explicit opt-in."""


def _provider():
    return build_provider("databento", cache=True)


def load_bars(symbol: str = C.DEV_INSTRUMENT, split: str = "dev",
              allow_holdout: bool = False) -> pd.DataFrame:
    """Load cached 5-min bars for one symbol, restricted to the requested split.
    split ∈ {"dev","holdout","all"}. Reading "holdout"/"all" requires
    allow_holdout=True (the boundary guard)."""
    if split not in ("dev", "holdout", "all"):
        raise ValueError(f"unknown split {split!r}")
    if split in ("holdout", "all") and not allow_holdout:
        raise HoldoutAccessError(
            f"split={split!r} touches the LOCKED hold-out; pass allow_holdout=True "
            "only in the single final evaluation."
        )
    bars = _provider().get_historical_bars(
        symbol, C.DATA_START.to_pydatetime(), C.HOLDOUT_END.to_pydatetime(), "5m"
    )
    if split == "dev":
        bars = bars[(bars.index >= C.DATA_START) & (bars.index <= C.DEV_END)]
    elif split == "holdout":
        bars = bars[(bars.index >= C.HOLDOUT_START) & (bars.index <= C.HOLDOUT_END)]
    return bars


def annotate_session(bars: pd.DataFrame) -> pd.DataFrame:
    """Add ET-session annotations without look-ahead:
      - et_date: ET calendar date (session key)
      - in_rth: within 09:30–16:00 ET
      - forecast_eligible: FORECAST_START ≤ ET ≤ FORECAST_LAST (10:00–15:25),
        i.e. OR fully formed and (by construction) room for a 30-min fwd window.
    Forward-window completeness itself is enforced in labels.py.
    """
    et = bars.index.tz_convert(C.TIMEZONE)
    t = et.time

    def _between_inclusive(a, b):
        lo, hi = dt_time(*a), dt_time(*b)
        return np.array([lo <= x <= hi for x in t])

    def _between_halfopen(a, b):
        lo, hi = dt_time(*a), dt_time(*b)
        return np.array([lo <= x < hi for x in t])

    out = bars.copy()
    out["et_date"] = et.date
    out["in_rth"] = _between_halfopen(C.RTH_OPEN, C.RTH_CLOSE)
    out["forecast_eligible"] = _between_inclusive(C.FORECAST_START, C.FORECAST_LAST)
    return out
