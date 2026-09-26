"""Market Structure — BOS / MSS (standalone, READ-ONLY).

Faithful Python port of the Pine Script "Market Structure [TFO]" indicator. Self-contained: imports
nothing from the trading engine, no side effects, never influences trade generation. A "main script"
(the serving path) calls `detect_market_structure(bars, ...)` and renders `summary(...)`.

Logic (identical to the indicator), replayed bar-by-bar:
  • symmetric swing pivots via `pivot_strength` bars each side (the last unbroken swing high/low).
  • a CLOSE above the last swing high is a break to the upside; a close below the last swing low is a
    break to the downside. Each swing is consumed by the first close that breaks it.
  • MSS (Market Structure Shift) when the break flips the prevailing direction (or it is the first
    break); BOS (Break of Structure) when it continues the prevailing direction.
      close > swing high: MSS if prior state was bearish/unknown, else BOS  -> state = bull
      close < swing low : MSS if prior state was bullish/unknown, else BOS  -> state = bear
Each event carries the broken swing level, the swing's bar index, and the bar the break closed on
(for a line from swing → break with an MSS/BOS label). Bull events are blue, bear events red.
Works on engine `Bar` objects or snapshot dicts ('o','h','l','c','t').
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _h(b): return float(b.high if hasattr(b, "high") else b["h"])
def _l(b): return float(b.low if hasattr(b, "low") else b["l"])
def _c(b): return float(b.close if hasattr(b, "close") else b["c"])
def _t(b):
    if hasattr(b, "open_time"):
        return b.open_time.isoformat()
    return b.get("t") if isinstance(b, dict) else None


@dataclass
class MSEvent:
    kind: str                       # "BOS" | "MSS"
    direction: str                  # "bull" | "bear"
    level: float                    # the broken swing price
    from_index: int                 # bar index of the swing that broke
    break_index: int                # bar index whose close broke it
    from_time: Optional[str]
    break_time: Optional[str]

    def to_dict(self) -> dict:
        return {"kind": self.kind, "direction": self.direction, "level": round(self.level, 2),
                "from_index": self.from_index, "break_index": self.break_index,
                "from_time": self.from_time, "break_time": self.break_time}


def _is_ph(bars, p, L):
    v = _h(bars[p])
    for k in range(1, L + 1):
        if _h(bars[p - k]) >= v or _h(bars[p + k]) >= v:
            return False
    return True


def _is_pl(bars, p, L):
    v = _l(bars[p])
    for k in range(1, L + 1):
        if _l(bars[p - k]) <= v or _l(bars[p + k]) <= v:
            return False
    return True


def detect_market_structure(bars, *, pivot_strength: int = 3) -> list[MSEvent]:
    """Replay the indicator over `bars`; return every BOS/MSS event in order."""
    n = len(bars)
    L = pivot_strength
    if n < 2 * L + 1:
        return []
    swing_high = None                                    # (index, value) — last unbroken swing high
    swing_low = None
    bull = None                                          # prevailing direction: True/False/None
    events: list[MSEvent] = []

    for c in range(n):
        p = c - L
        if p - L >= 0:                                   # p has L bars each side (right side up to c)
            if _is_ph(bars, p, L):
                swing_high = (p, _h(bars[p]))
            if _is_pl(bars, p, L):
                swing_low = (p, _l(bars[p]))

        close_c = _c(bars[c])
        if swing_high is not None and close_c > swing_high[1]:      # break to the upside
            mss = bull is False or bull is None
            events.append(MSEvent("MSS" if mss else "BOS", "bull", swing_high[1], swing_high[0],
                                  c, _t(bars[swing_high[0]]), _t(bars[c])))
            bull = True
            swing_high = None
        if swing_low is not None and close_c < swing_low[1]:        # break to the downside
            mss = bull is True or bull is None
            events.append(MSEvent("MSS" if mss else "BOS", "bear", swing_low[1], swing_low[0],
                                  c, _t(bars[swing_low[0]]), _t(bars[c])))
            bull = False
            swing_low = None

    return events


def detect_pivots(bars, *, pivot_strength: int = 3) -> list[dict]:
    """All confirmed swing pivots (high ▼ / low ▲), for display. Only pivots with `pivot_strength`
    bars on each side are returned (the last few bars can't be confirmed yet)."""
    n = len(bars)
    L = pivot_strength
    out = []
    for p in range(L, n - L):
        if _is_ph(bars, p, L):
            out.append({"index": p, "price": round(_h(bars[p]), 2), "kind": "high", "time": _t(bars[p])})
        if _is_pl(bars, p, L):
            out.append({"index": p, "price": round(_l(bars[p]), 2), "kind": "low", "time": _t(bars[p])})
    return out


def summary(events: list[MSEvent], limit: int = 20) -> dict:
    """The most recent `limit` events (for drawing) + per-type counts."""
    def n(kind, direction):
        return sum(1 for e in events if e.kind == kind and e.direction == direction)
    recent = events[-limit:] if limit else events
    return {"events": [e.to_dict() for e in recent],
            "bull_mss": n("MSS", "bull"), "bull_bos": n("BOS", "bull"),
            "bear_mss": n("MSS", "bear"), "bear_bos": n("BOS", "bear"),
            "total": len(events)}
