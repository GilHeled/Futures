"""Session / killzone membership (ET, DST-safe). Read from config.SESSIONS.

Distinguishes the ACTIVE trading windows (killzones, config C1) from full sessions: this
module answers window membership only; full-session high/low pools are a separate (later)
liquidity concern. DST is automatic (ET wall-clock via zoneinfo). Windows may wrap midnight
(Asia 18:00->00:00).
"""
from __future__ import annotations

from datetime import datetime, time

from ict_live import config as C
from ict_live.market.calendar import ET


def _in_window(t: time, start: time, end: time) -> bool:
    # end==00:00 means midnight (24:00); treat as wrap to end-of-day
    if (start, end) == (start, time(0, 0)):
        return t >= start
    if start <= end:
        return start <= t < end
    return t >= start or t < end   # wraps midnight


def active_windows(dt: datetime) -> list[str]:
    """Names of config.SESSIONS windows active at `dt` (ET)."""
    t = dt.astimezone(ET).timetz().replace(tzinfo=None)
    return [name for name, (s, e) in C.SESSIONS.items() if _in_window(t, s, e)]


def in_session(dt: datetime, name: str) -> bool:
    s, e = C.SESSIONS[name]
    t = dt.astimezone(ET).timetz().replace(tzinfo=None)
    return _in_window(t, s, e)


def killzone(dt: datetime) -> str | None:
    """The active TRADING killzone at dt, if any (london_active / ny_am / ny_pm).
    'asia' is a liquidity-reference window, not a trading killzone."""
    for name in ("london_active", "ny_am", "ny_pm"):
        if in_session(dt, name):
            return name
    return None
