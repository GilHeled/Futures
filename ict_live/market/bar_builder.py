"""Deterministic, causal 1-minute -> higher-timeframe resampler.

Fed completed 1-minute bars in order; emits higher-TF bars ONLY when they close. The
still-aggregating bucket is available via `forming(tf)` and is labelled forming=True so it
can never confirm a signal. Pure function of the 1m stream (no wall-clock, no future data)
=> replaying the same 1m prefix reproduces identical closed HTF bars (prefix-stability).

Intraday TFs (5m/15m/1H/4H) are ET-clock-aligned. "D" follows the CME SESSION DAY and "W"
the trading week (via market.calendar) — NOT midnight-to-midnight ET.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional

from ict_live.market.bar import Bar
from ict_live.market.calendar import ET, Calendar

_MINUTES = {"5m": 5, "15m": 15, "1H": 60, "4H": 240}


def _floor_intraday(dt: datetime, tf: str) -> datetime:
    """Wall-clock ET floor (DST-safe; no elapsed-timedelta arithmetic)."""
    dt = dt.astimezone(ET)
    m = _MINUTES[tf]
    if m >= 60:
        fh = (dt.hour // (m // 60)) * (m // 60)
        return datetime(dt.year, dt.month, dt.day, fh, 0, tzinfo=ET)
    tot = dt.hour * 60 + dt.minute
    fl = (tot // m) * m
    return datetime(dt.year, dt.month, dt.day, fl // 60, fl % 60, tzinfo=ET)


class _Agg:
    __slots__ = ("start", "end", "o", "h", "l", "c", "v", "last_close")

    def __init__(self, start, end, b: Bar):
        self.start, self.end = start, end
        self.o, self.h, self.l, self.c, self.v = b.open, b.high, b.low, b.close, b.volume
        self.last_close = b.close_time

    def update(self, b: Bar):
        self.h = max(self.h, b.high); self.l = min(self.l, b.low)
        self.c = b.close; self.v += b.volume; self.last_close = b.close_time

    def to_bar(self, tf: str, forming: bool) -> Bar:
        return Bar(tf, self.start, self.end if not forming else self.last_close,
                   self.o, self.h, self.l, self.c, self.v, forming=forming)


class BarBuilder:
    def __init__(self, timeframes=("5m", "15m", "1H", "4H", "D", "W"), calendar: Optional[Calendar] = None):
        self.timeframes = tuple(timeframes)
        self.cal = calendar or Calendar()
        self._cur: dict[str, Optional[_Agg]] = {tf: None for tf in self.timeframes}
        self._last_1m_open: Optional[datetime] = None

    def _bounds(self, tf: str, open_time: datetime):
        if tf in _MINUTES:
            s = _floor_intraday(open_time, tf)
            return s, s + timedelta(minutes=_MINUTES[tf])
        if tf == "D":
            sd = self.cal.session_day(open_time)
            return self.cal.day_bounds(sd) if sd is not None else (None, None)
        if tf == "W":
            if self.cal.session_day(open_time) is None:
                return None, None
            return self.cal.week_bounds(open_time)
        raise ValueError(f"unsupported timeframe {tf!r}")

    def add_1m(self, b: Bar) -> list[Bar]:
        if b.timeframe != "1m":
            raise ValueError("BarBuilder.add_1m expects a 1m bar")
        if self._last_1m_open is not None and b.open_time <= self._last_1m_open:
            return []                                  # out-of-order/dup (validated upstream)
        self._last_1m_open = b.open_time

        closed: list[Bar] = []
        for tf in self.timeframes:
            start, end = self._bounds(tf, b.open_time)
            if start is None:                          # bar not part of any D/W session bucket
                continue
            cur = self._cur[tf]
            if cur is not None and start > cur.start:  # a later bucket began (gap): finalize prior
                closed.append(cur.to_bar(tf, forming=False)); cur = None
            if cur is None:
                cur = _Agg(start, end, b)
            elif start == cur.start:
                cur.update(b)
            if b.close_time >= cur.end:                # this bar completes the bucket
                closed.append(cur.to_bar(tf, forming=False)); cur = None
            self._cur[tf] = cur
        return closed

    def forming(self, tf: str) -> Optional[Bar]:
        cur = self._cur.get(tf)
        return cur.to_bar(tf, forming=True) if cur is not None else None
