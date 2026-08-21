"""Market Structure Shift (MSS) — EXPERIMENTAL; nothing frozen.

Built on frozen decision B6: the swing that must break is the most recent CONFIRMED STRUCTURAL
opposing swing that defines the PRE-manipulation structure (belongs to the analysis TF, existed
before the manipulation began, represents the structure being reversed) — NOT merely the last
confirmed swing. The break is a BODY CLOSE through that swing.

Three states (B6): Potential (target identified, no penetration) → Candidate (wick penetrated, no
body close yet) → Confirmed (body close beyond the swing). A diagnostic acceptance distance is
recorded but kept separate from the state (B6: keep displacement/acceptance quality separate).

Objective + causal: the shift is produced by a displacement leg; scanning is only forward from the
manipulation. Each MSS exposes `why` and `depends_on` (the displacement, the broken structural
swing, and — when confirmed — the body-close bar).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ict_live.market.bar import Bar
from ict_live.structure import ids
from ict_live.structure.displacement import Displacement
from ict_live.structure.swings import Swing


@dataclass(frozen=True)
class MSS:
    id: str
    direction: str            # "bearish" (broke a structural low) | "bullish" (broke a high)
    state: str                # "potential" | "candidate" | "confirmed"
    broken_price: float
    broken_index: int
    confirm_index: Optional[int]   # body-close bar, if confirmed
    acceptance: float         # close distance beyond the swing (diagnostic; 0 unless confirmed)
    reason: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)


def detect_mss(displacements: list[Displacement], structural: list[Swing],
               bars: list[Bar]) -> list[MSS]:
    out: list[MSS] = []
    for d in displacements:
        # the pre-manipulation structural swing being reversed (opposing side, before the manip)
        if d.direction == "bearish":
            cands = [s for s in structural if s.kind == "low" and s.index < d.start_index]
        else:
            cands = [s for s in structural if s.kind == "high" and s.index < d.start_index]
        if not cands:
            continue
        target = max(cands, key=lambda s: s.index)      # most recent pre-manip structural swing

        penetrated, confirm = False, None
        for i in range(d.start_index + 1, len(bars)):
            b = bars[i]
            if d.direction == "bearish":
                if b.low < target.price:
                    penetrated = True
                if b.close < target.price:
                    confirm = i
                    break
            else:
                if b.high > target.price:
                    penetrated = True
                if b.close > target.price:
                    confirm = i
                    break
        state = "confirmed" if confirm is not None else ("candidate" if penetrated else "potential")
        acceptance = round(abs(target.price - bars[confirm].close), 2) if confirm is not None else 0.0

        deps = [d.id, ids.swing_id(target)]
        if confirm is not None:
            deps.append(ids.bar_id(confirm))
        side = "structural low" if d.direction == "bearish" else "structural high"
        if state == "confirmed":
            tail = (f"body close {bars[confirm].close:g} beyond it at bar {confirm} "
                    f"(acceptance {acceptance:g}) → structure shifted {d.direction}")
        elif state == "candidate":
            tail = "wick penetrated but no body close yet → candidate"
        else:
            tail = "no penetration yet → potential"
        why = (f"{d.direction} MSS: pre-manipulation {side} {target.price:g} (bar {target.index}) "
               f"is the structure being reversed; {tail}. Depends on displacement {d.id}.")
        out.append(MSS(
            id=ids.mss_id(d.start_index, target.index, d.direction),
            direction=d.direction, state=state, broken_price=float(target.price),
            broken_index=target.index, confirm_index=confirm, acceptance=acceptance,
            reason=why, depends_on=tuple(deps),
        ))
    return out
