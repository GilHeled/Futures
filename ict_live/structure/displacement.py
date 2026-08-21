"""Displacement — the impulsive leg away from a manipulation (EXPERIMENTAL; nothing frozen).

Built on frozen decision B5: the displacement leg STARTS at the MANIPULATION EXTREME and ENDS at
impulse exhaustion, defined as the first CONFIRMED width-1 counter-pivot after the manipulation
(a minor swing low ends a bearish impulse; a minor swing high ends a bullish one). END is NOT the
MSS close — that is a separate layer. The swept pool and sweep bar are kept distinct from this leg.

Objective + causal: uses only bars from the manipulation forward; the width-1 counter-pivot is
confirmed one bar after it prints. Each Displacement exposes `why` and `depends_on` (the Sweep).
"Displacement quality" (energetic vs weak) is left to RANKING (modular evaluators), not a hard
gate here — config.DISPLACEMENT_QUALITY stays a sentinel. This detector produces candidates only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ict_live.market.bar import Bar
from ict_live.structure import ids
from ict_live.structure.manipulation import Sweep
from ict_live.structure.swings import SwingDetector


@dataclass(frozen=True)
class Displacement:
    id: str
    direction: str            # "bearish" (down impulse) | "bullish" (up impulse)
    start_index: int          # manipulation-extreme bar
    end_index: int            # impulse-exhaustion bar (first width-1 counter-pivot), or last bar
    start_price: float        # manipulation extreme
    end_price: float          # counter-pivot extreme
    net: float                # move magnitude in the impulse direction (price)
    span: int                 # bars from start to end
    exhausted: bool           # True if a counter-pivot ended it (else still in progress at cursor)
    reason: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)


def detect_displacements(sweeps: list[Sweep], bars: list[Bar]) -> list[Displacement]:
    det = SwingDetector(1)                       # width-1 minor pivots = impulse-exhaustion markers
    for b in bars:
        det.add(b)
    minor = det.confirmed()
    lows = sorted(s.index for s in minor if s.kind == "low")
    highs = sorted(s.index for s in minor if s.kind == "high")

    out: list[Displacement] = []
    for sw in sweeps:
        j = sw.bar_index
        ends = [i for i in (lows if sw.direction == "bearish" else highs) if i > j]
        counter = ends[0] if ends else None
        exhausted = counter is not None
        end = counter if counter is not None else len(bars) - 1
        if end <= j:
            continue
        start_price = sw.extreme
        if sw.direction == "bearish":
            end_price = bars[end].low
            net = start_price - end_price
        else:
            end_price = bars[end].high
            net = end_price - start_price
        if net <= 0:                             # no genuine move in the impulse direction
            continue
        span = end - j
        exh = ("exhausted at first width-1 counter-pivot" if exhausted
               else "still in progress at cursor (no counter-pivot yet)")
        why = (f"{sw.direction} displacement from manipulation extreme {start_price:g} (bar {j}) "
               f"to {end_price:g} (bar {end}); net {net:g} over {span} bars; {exh}. "
               f"Depends on manipulation {sw.id}.")
        out.append(Displacement(
            id=ids.displacement_id(j, end, sw.direction),
            direction=sw.direction, start_index=j, end_index=end,
            start_price=float(start_price), end_price=float(end_price),
            net=float(net), span=span, exhausted=exhausted, reason=why,
            depends_on=(sw.id,),
        ))
    return out
