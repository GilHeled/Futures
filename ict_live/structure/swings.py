"""Streaming, causal fractal swing detection (the primitive under all structure/liquidity).

A bar at index i is a swing high (low) if its high (low) is the strict-or-equal extreme of
the centered window [i-w, i+w], where w = FRACTAL_WIDTH for the timeframe. Crucially the
pivot at i is only KNOWABLE once bar i+w has closed, so it is emitted `w` bars late and
carries `confirm_index`. This mirrors mnq_system.swings but operates on the live closed-bar
stream so replaying a 1m prefix (=> same closed HTF bars) yields identical, identically-timed
swings (prefix-stability).

This detector produces CANDIDATE structural pivots. It does NOT decide "significant swing"
(that gate is `config.SIGNIFICANT_SWING_MAGNITUDE`, still deferred) — callers must not treat
a raw pivot as a dealing-range endpoint / ERL / MSS swing without that separate decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ict_live.market.bar import Bar


@dataclass(frozen=True)
class Swing:
    kind: str            # "high" | "low"
    index: int           # position in the TF's closed-bar stream where the pivot sits
    confirm_index: int   # position at which it became knowable (index + width)
    time: datetime       # open_time of the pivot bar
    price: float         # high (kind=="high") or low (kind=="low")


class SwingDetector:
    """Fed CLOSED bars of a single timeframe in order via `add(bar)`; returns the list of
    swings newly CONFIRMED by that bar (usually empty or one). Forming bars must not be fed."""

    def __init__(self, width: int):
        if width < 1:
            raise ValueError("fractal width must be >= 1")
        self.width = width
        self._bars: list[Bar] = []
        self._emitted: set[tuple[str, int]] = set()

    def add(self, bar: Bar) -> list[Swing]:
        if bar.forming:
            raise ValueError("SwingDetector consumes closed bars only")
        self._bars.append(bar)
        n = len(self._bars)
        w = self.width
        # the pivot that can newly confirm now sits `w` bars back from the last index
        c = n - 1 - w
        if c < w:                       # need w neighbors on each side
            return []
        out: list[Swing] = []
        for kind in ("high", "low"):
            if (kind, c) in self._emitted:
                continue
            if self._is_pivot(c, kind):
                self._emitted.add((kind, c))
                px = self._bars[c].high if kind == "high" else self._bars[c].low
                out.append(Swing(kind, c, n - 1, self._bars[c].open_time, px))
        return out

    def _is_pivot(self, c: int, kind: str) -> bool:
        w = self.width
        if kind == "high":
            p = self._bars[c].high
            # strict vs left neighbors, >= vs right neighbors -> first-occurrence wins on ties
            return all(self._bars[j].high < p for j in range(c - w, c)) and \
                   all(self._bars[j].high <= p for j in range(c + 1, c + w + 1))
        p = self._bars[c].low
        return all(self._bars[j].low > p for j in range(c - w, c)) and \
               all(self._bars[j].low >= p for j in range(c + 1, c + w + 1))

    def confirmed(self) -> list[Swing]:
        """All swings confirmed so far, in confirmation order (diagnostic/replay)."""
        out = []
        for (kind, c) in sorted(self._emitted, key=lambda x: (x[1], x[0])):
            px = self._bars[c].high if kind == "high" else self._bars[c].low
            out.append(Swing(kind, c, c + self.width, self._bars[c].open_time, px))
        return out
