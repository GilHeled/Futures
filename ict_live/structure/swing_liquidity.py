"""Swing-derived liquidity: turn OBJECTIVE structural swings into ERL pools whose relevance is
CONTEXT-DEPENDENT (per the methodology owner — significance is liquidity, not a fixed swing tag).

A structural swing high is buy-side liquidity resting ABOVE it; a structural swing low is
sell-side liquidity resting BELOW it. Each pool is `active` (resting / unswept — a potential draw)
until a later bar's WICK trades through the level, after which it is `swept` (liquidity taken).
Sweep uses the wick (high/low), not the close — a sweep is a raid, not necessarily acceptance.

Causal: a pool's swept state is decided only by bars AFTER the swing formed. This is objective and
frozen-parameter-free (no magnitude, no ATR); the "which ERL is THE draw" judgment (premium/
discount location, equal highs, HTF alignment) layers on top later and is not decided here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ict_live.market.bar import Bar
from ict_live.structure.swings import Swing


@dataclass(frozen=True)
class SwingPool:
    kind: str                 # "high" (buy-side) | "low" (sell-side)
    price: float
    time: datetime            # when the swing formed
    index: int                # pivot bar position
    swept: bool               # a later wick traded through the level
    swept_index: Optional[int]  # bar position that swept it, if any
    reason: str = ""          # human-readable WHY (for the visual audit / decision log)


def swing_liquidity(structural: list[Swing], bars: list[Bar]) -> list[SwingPool]:
    pools: list[SwingPool] = []
    for s in structural:
        side = "buy-side (BSL)" if s.kind == "high" else "sell-side (SSL)"
        swept_at = None
        for j in range(s.index + 1, len(bars)):
            b = bars[j]
            if (s.kind == "high" and b.high > s.price) or (s.kind == "low" and b.low < s.price):
                swept_at = j
                break
        if swept_at is None:
            why = (f"structural {s.kind}; {side} liquidity still resting (unswept as of cursor) "
                   f"→ active draw")
        else:
            why = (f"structural {s.kind}; {side} liquidity taken — wick traded through "
                   f"{s.price:g} at bar {swept_at} ({bars[swept_at].open_time.isoformat()})")
        pools.append(SwingPool(s.kind, float(s.price), s.time, s.index,
                               swept=swept_at is not None, swept_index=swept_at, reason=why))
    return pools


def active(pools: list[SwingPool]) -> list[SwingPool]:
    """Unswept pools — resting liquidity, the current draws."""
    return [p for p in pools if not p.swept]


def swept(pools: list[SwingPool]) -> list[SwingPool]:
    return [p for p in pools if p.swept]
