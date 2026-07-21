"""
Data loading + BOUNDARY ENFORCEMENT. Two guarantees:

  1. The locked hold-out (2025-01-01 → 2026-07-09) is inaccessible unless a
     caller explicitly passes allow_holdout=True — so it cannot be touched
     during development by accident. Phase 1a/1b use split="dev" only;
     Phase 1c is the single place allow_holdout=True is permitted.
  2. Only the tradable RTH window is exposed for entries (10:00–15:00 ET),
     with the 15:55 ET force-flat bar marked — no overnight, no pre-10:00
     entries (opening-range features aren't formed yet).

Reuses the existing CachingProvider (already-paid cached Databento bars);
no new data fetch.
"""
from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from intraday_alerts import config as C
from mnq_system.data.providers import build_provider


class HoldoutAccessError(RuntimeError):
    """Raised on any attempt to read the locked hold-out without explicit opt-in."""


def _provider():
    return build_provider("databento", cache=True)


def load_bars(symbol: str, split: str = "dev", allow_holdout: bool = False) -> pd.DataFrame:
    """Load cached 5-min bars for one symbol, restricted to the requested
    split. split ∈ {"dev","holdout","all"}. Reading "holdout"/"all" requires
    allow_holdout=True (the boundary guard)."""
    if split not in ("dev", "holdout", "all"):
        raise ValueError(f"unknown split {split!r}")
    if split in ("holdout", "all") and not allow_holdout:
        raise HoldoutAccessError(
            f"split={split!r} touches the LOCKED hold-out; pass allow_holdout=True "
            "only in the single Phase-1c final evaluation."
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
      - et: tz-converted index
      - in_rth: within 09:30–16:00 ET
      - entry_eligible: 10:00 ≤ ET < 15:00 (OR fully formed, before flat window)
      - force_flat: the 15:55 ET bar (mandatory exit)
    """
    et = bars.index.tz_convert(C.TIMEZONE)
    t = et.time
    def _between(a, b):
        return np.array([dt_time(*a) <= x < dt_time(*b) for x in t])
    in_rth = _between((9, 30), (16, 0))
    entry_eligible = _between(C.ENTRY_START, C.ENTRY_END)
    force_flat = np.array([x >= dt_time(*C.FLAT_BY) and dt_time(*C.FLAT_BY) <= x < dt_time(16, 0) for x in t])
    out = bars.copy()
    out["et_date"] = et.date
    out["in_rth"] = in_rth
    out["entry_eligible"] = entry_eligible
    out["force_flat"] = force_flat
    return out
