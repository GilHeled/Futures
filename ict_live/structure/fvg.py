"""Fair Value Gap (FVG) — EXPERIMENTAL; nothing frozen. Geometry is course-resolved (A1).

A1 geometry: bullish FVG when high[i-1] < low[i+1] (gap up); bearish when low[i-1] > high[i+1]
(gap down). CE (consequent encroachment) = 50% of the gap. A5/B5 "same-leg": only FVGs that sit
INSIDE the displacement leg that produced the MSS are eligible (scan the leg, allowing completion
at k+1). A3 mitigation: a body close through the FAR boundary invalidates the gap.

Causal: an FVG is knowable when its third candle (i+1) closes; fill/mitigation only ever look at
bars AFTER that. Each FVG exposes `why` and `depends_on` (the displacement and the MSS).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ict_live.market.bar import Bar
from ict_live.structure import ids
from ict_live.structure.displacement import Displacement
from ict_live.structure.mss import MSS


@dataclass(frozen=True)
class FVG:
    id: str
    direction: str            # "bullish" | "bearish"
    top: float
    bottom: float
    ce: float                 # 50% (consequent encroachment) — the entry reference (B8)
    mid_index: int            # middle candle of the 3
    formed_index: int         # i+1, when the gap becomes known
    formed_time: datetime
    status: str               # "unfilled" | "touched" (reached CE) | "mitigated" (invalidated)
    first_touch_index: Optional[int]
    reason: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)


def _disp_map(displacements: list[Displacement]) -> dict[str, Displacement]:
    return {d.id: d for d in displacements}


def detect_fvgs(mss_list: list[MSS], displacements: list[Displacement],
                bars: list[Bar]) -> list[FVG]:
    dmap = _disp_map(displacements)
    out: list[FVG] = []
    seen: set[str] = set()
    for m in mss_list:
        d = dmap.get(m.depends_on[0] if m.depends_on else None)
        if d is None:
            continue
        for i in range(d.start_index + 1, d.end_index):          # middle candle i (needs i-1,i+1)
            if i + 1 >= len(bars):
                break
            a, c = bars[i - 1], bars[i + 1]
            if d.direction == "bullish" and a.high < c.low:
                top, bottom = c.low, a.high
            elif d.direction == "bearish" and a.low > c.high:
                top, bottom = a.low, c.high
            else:
                continue
            fid = ids.fvg_id(i, d.direction)
            if fid in seen:
                continue
            seen.add(fid)
            ce = (top + bottom) / 2.0
            formed = i + 1
            first_touch, mitigated = None, False
            for k in range(formed + 1, len(bars)):
                b = bars[k]
                if d.direction == "bearish":
                    if first_touch is None and b.high >= ce:      # returned up to CE
                        first_touch = k
                    if b.close > top:                             # body close above far boundary
                        mitigated = True
                        break
                else:
                    if first_touch is None and b.low <= ce:
                        first_touch = k
                    if b.close < bottom:
                        mitigated = True
                        break
            status = "mitigated" if mitigated else ("touched" if first_touch is not None else "unfilled")
            why = (f"{d.direction} FVG [{bottom:g}, {top:g}] (CE {ce:g}) formed at bar {formed} "
                   f"inside displacement {d.id}; status={status}"
                   + (f", first reached CE at bar {first_touch}" if first_touch is not None else "")
                   + f". Depends on displacement {d.id} and MSS {m.id}.")
            out.append(FVG(
                id=fid, direction=d.direction, top=float(top), bottom=float(bottom), ce=float(ce),
                mid_index=i, formed_index=formed, formed_time=bars[formed].open_time,
                status=status, first_touch_index=first_touch, reason=why,
                depends_on=(d.id, m.id),
            ))
    return out
