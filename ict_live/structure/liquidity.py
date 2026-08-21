"""Objective liquidity pools (External Range Liquidity, ERL).

Tracks the liquidity levels that are DEFINED BY PERIOD/SESSION EXTREMES and therefore need no
discretionary threshold: prior-day high/low (PDH/PDL), prior-week high/low (PWH/PWL), and the
completed-session highs/lows (Asia/London/NY-AM/NY-PM). Each pool is finalized only once its
period has closed (causal): PDH/PDL update when a Daily bar closes, PWH/PWL when a Weekly bar
closes, session pools when the session window ends.

DELIBERATELY EXCLUDED here (they depend on still-deferred decisions, kept as hard sentinels):
  * equal-highs/equal-lows clustering  -> needs config.EQUAL_HL_TOL_ATR (+ an ATR period)
  * swing-liquidity / "significant swing" pools -> needs config.SIGNIFICANT_SWING_MAGNITUDE
  * IRL pools (FVG / NWOG / ORG) -> built with the FVG layer
These are added only after their parameters are frozen; nothing here silently chooses a value.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from datetime import timedelta

from ict_live import config as C
from ict_live.market.bar import Bar
from ict_live.market import sessions as S

# session windows that yield a liquidity pool when they complete (all four, incl. Asia
# reference). Trading-vs-reference distinction lives in sessions.killzone, not here.
_SESSION_POOLS = ("asia", "london_active", "ny_am", "ny_pm")


@dataclass(frozen=True)
class Pool:
    name: str            # e.g. "PDH", "PWL", "ASIA_H", "NY_AM_L"
    kind: str            # "high" | "low"
    price: float
    erl: bool            # External Range Liquidity (all pools here are ERL)
    source: str          # "daily" | "weekly" | "session:<name>"
    formed_time: datetime  # when the defining period closed (causal availability)


class LiquidityRegistry:
    def __init__(self):
        self._daily_prev: Optional[Bar] = None
        self._weekly_prev: Optional[Bar] = None
        self._pdh: Optional[Pool] = None
        self._pdl: Optional[Pool] = None
        self._pwh: Optional[Pool] = None
        self._pwl: Optional[Pool] = None
        # active session accumulators: name -> (session_key, hi, lo, hi_open, lo_open)
        self._acc: dict[str, dict] = {}
        self._session_pools: dict[str, tuple[Pool, Pool]] = {}   # name -> (high, low)

    # ---- period extremes ----
    def on_daily_close(self, d: Bar) -> None:
        """A just-CLOSED daily bar becomes the prior day; its extremes are PDH/PDL."""
        self._pdh = Pool("PDH", "high", d.high, True, "daily", d.close_time)
        self._pdl = Pool("PDL", "low", d.low, True, "daily", d.close_time)
        self._daily_prev = d

    def on_weekly_close(self, w: Bar) -> None:
        self._pwh = Pool("PWH", "high", w.high, True, "weekly", w.close_time)
        self._pwl = Pool("PWL", "low", w.low, True, "weekly", w.close_time)
        self._weekly_prev = w

    # ---- session extremes (fed the 1m stream) ----
    def on_1m(self, b: Bar) -> None:
        for name in _SESSION_POOLS:
            inside = S.in_session(b.open_time, name)
            acc = self._acc.get(name)
            if inside:
                key = self._session_key(b.open_time, name)
                if acc is None or acc["key"] != key:
                    if acc is not None:
                        self._finalize_session(name, acc)     # previous instance ended
                    acc = {"key": key, "hi": b.high, "lo": b.low,
                           "hi_t": b.open_time, "lo_t": b.open_time}
                    self._acc[name] = acc
                else:
                    if b.high > acc["hi"]:
                        acc["hi"], acc["hi_t"] = b.high, b.open_time
                    if b.low < acc["lo"]:
                        acc["lo"], acc["lo_t"] = b.low, b.open_time
            else:
                if acc is not None:
                    self._finalize_session(name, acc)
                    self._acc.pop(name, None)

    def _session_key(self, dt: datetime, name: str):
        # One key per session instance. For a window that wraps past midnight (start>end),
        # the after-midnight tail is anchored back to the evening-start ET date so both halves
        # share a key. (Under current config Asia ends at 00:00, so no tail arises — but this
        # keeps the key correct if a wrapping window is ever configured.)
        et = dt.astimezone(S.ET)
        start, end = C.SESSIONS[name]
        d = et.date()
        if start > end and et.timetz().replace(tzinfo=None) < end:
            d = d - timedelta(days=1)
        return (name, d)

    def _finalize_session(self, name: str, acc: dict) -> None:
        label = {"asia": "ASIA", "london_active": "LONDON",
                 "ny_am": "NY_AM", "ny_pm": "NY_PM"}[name]
        ft = max(acc["hi_t"], acc["lo_t"])
        self._session_pools[name] = (
            Pool(f"{label}_H", "high", acc["hi"], True, f"session:{name}", ft),
            Pool(f"{label}_L", "low", acc["lo"], True, f"session:{name}", ft),
        )

    # ---- access ----
    def pools(self) -> list[Pool]:
        out = [p for p in (self._pdh, self._pdl, self._pwh, self._pwl) if p is not None]
        for hi, lo in self._session_pools.values():
            out += [hi, lo]
        return out
