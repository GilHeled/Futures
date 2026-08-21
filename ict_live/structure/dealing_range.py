"""Dealing-range DETECTION (EXPERIMENTAL; nothing frozen).

Same split as structural-swings vs liquidity-relevance: this module only DISCOVERS objective
structural dealing ranges — it does NOT decide which one is "the" active range. There is no single
dealing range; the methodology is hierarchical, so at any instant there is one dealing range PER
structural timeframe (W / D / 4H / 1H / 15m …). The CONTEXT layer (later) decides which range(s)
a given decision uses, and every downstream object must state its dealing-range source. No hidden
algorithm here picks a winner.

A dealing range on a timeframe = the current leg price is working within on that TF: the most
recent completed leg between that TF's last two opposing STRUCTURAL swings. Premium = upper half,
Discount = lower half, split at CE (50% / equilibrium). Objective and causal.

TENSION (flagged, NOT resolved): frozen B2 speaks of "the most recent MEANINGFUL completed
structural swing leg" (singular). This module intentionally produces the per-TF SET instead and
leaves relevance to context; the B2 wording must be reconciled (with the user) before freezing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ict_live.structure.swings import Swing


@dataclass(frozen=True)
class DealingRange:
    source_tf: str            # the structural timeframe this range belongs to ("1H","D",...)
    low: float
    high: float
    low_time: datetime
    high_time: datetime
    direction: str            # "up" (low→high most recent) | "down" (high→low most recent)
    ce: float                 # consequent encroachment / equilibrium (50%)
    start_time: datetime
    end_time: datetime
    reason: str = ""

    def zone_of(self, price: float) -> str:
        if price > self.ce:
            return "premium"
        if price < self.ce:
            return "discount"
        return "equilibrium"


def range_for_tf(structural: list[Swing], source_tf: str) -> Optional[DealingRange]:
    """The current objective dealing range on ONE timeframe (its most recent completed structural
    leg). None if that TF lacks both a structural high and low, or the pair is degenerate."""
    highs = [s for s in structural if s.kind == "high"]
    lows = [s for s in structural if s.kind == "low"]
    if not highs or not lows:
        return None
    last_high, last_low = highs[-1], lows[-1]
    hi_p, lo_p = float(last_high.price), float(last_low.price)
    if hi_p <= lo_p:
        return None
    ce = (hi_p + lo_p) / 2.0
    direction = "up" if last_high.index > last_low.index else "down"
    start = last_low if direction == "up" else last_high
    end = last_high if direction == "up" else last_low
    why = (f"[{source_tf}] most recent completed structural leg: "
           f"{'low→high' if direction == 'up' else 'high→low'} "
           f"({lo_p:g} @ {last_low.time.isoformat()} ↔ {hi_p:g} @ {last_high.time.isoformat()}); "
           f"CE(50%)={ce:g}. Premium above CE, Discount below. (Which TF's range is relevant is a "
           f"CONTEXT decision, not made here.)")
    return DealingRange(source_tf, lo_p, hi_p, last_low.time, last_high.time, direction, ce,
                        start.time, end.time, reason=why)


def dealing_ranges(structural_by_tf: dict[str, list[Swing]]) -> list[DealingRange]:
    """Discover every valid structural dealing range across timeframes (the hierarchy). One entry
    per TF that has a valid range; each tagged with its `source_tf`. Context selects later."""
    out = []
    for tf, structural in structural_by_tf.items():
        dr = range_for_tf(structural, tf)
        if dr is not None:
            out.append(dr)
    return out
