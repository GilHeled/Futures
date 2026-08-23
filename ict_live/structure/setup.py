"""Setup assembly (Entry / Stop / Target) + Recommendation — EXPERIMENTAL; nothing frozen beyond
the course-resolved rules it cites.

Assembles a complete trade idea from the dependency chain:
  * entry  = FVG CE (B8 default entry mode).
  * stop   = the MANIPULATION EXTREME (A8: stop beyond it). STOP_BUFFER is deferred → no buffer
             applied yet (marked provisional; stop sits exactly at the extreme for now).
  * target = the next meaningful OPPOSING liquidity (B4): nearest ACTIVE ERL in the trade
             direction. RR to that real liquidity destination; actionable only if RR ≥ MIN_RR (B4),
             and the target is kept (not truncated to exactly 3R).
Geometry invariants (A8): entry beyond the manipulation extreme on the correct side; invalid ⇒
REJECT (never adjust). A mitigated FVG is rejected (invalidated). Every setup carries `why` and the
FULL `depends_on` chain (FVG, MSS, manipulation, dealing range, target pool) so a recommendation
traces to raw structure. All setups are RANKED, not filtered; the recommendation names the top
ACTIONABLE one, else NO-TRADE with the per-setup reasons.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ict_live import config as C
from ict_live.structure import ids
from ict_live.structure.displacement import Displacement
from ict_live.structure.fvg import FVG
from ict_live.structure.manipulation import Sweep
from ict_live.structure.swing_liquidity import SwingPool


@dataclass(frozen=True)
class Setup:
    id: str
    direction: str            # "long" | "short"
    entry: float
    stop: float
    target: Optional[float]
    rr: float
    risk: float
    reward: float
    actionable: bool
    reject_reason: str        # "" if actionable
    reason: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    structure_tf: str = ""    # TF the sweep/MSS/structure live on
    entry_tf: str = ""        # TF the entry FVG was refined on (== structure_tf if not refined)


@dataclass(frozen=True)
class Recommendation:
    decision: str             # "LONG" | "SHORT" | "NO-TRADE"
    setup: Optional[Setup]
    reason: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)


def build_setups(fvgs: list[FVG], disp_by_id: dict, sweep_by_id: dict,
                 active_erl: list[SwingPool], dr_id: Optional[str],
                 structure_tf: str = "", min_stop: Optional[float] = None) -> list[Setup]:
    out: list[Setup] = []
    for f in fvgs:
        d = disp_by_id.get(f.depends_on[0])
        mss_ident = f.depends_on[1] if len(f.depends_on) > 1 else None
        sweep = sweep_by_id.get(d.depends_on[0]) if d else None
        if d is None or sweep is None:
            continue
        direction = "short" if f.direction == "bearish" else "long"
        entry = f.ce
        stop = sweep.extreme                              # A8: at the manipulation extreme
        if direction == "short":
            cands = [p for p in active_erl if p.kind == "low" and p.price < entry]
            target_pool = max(cands, key=lambda p: p.price) if cands else None   # nearest below
        else:
            cands = [p for p in active_erl if p.kind == "high" and p.price > entry]
            target_pool = min(cands, key=lambda p: p.price) if cands else None   # nearest above
        target = target_pool.price if target_pool else None

        risk = abs(stop - entry)
        reward = abs(entry - target) if target is not None else 0.0
        rr = round(reward / risk, 2) if risk > 0 else 0.0

        reject = ""
        if min_stop is not None and risk < min_stop:
            reject = f"degenerate stop — risk {round(risk,2)} < execution floor {min_stop:g} (unrealistic)"
        elif f.status == "mitigated":
            reject = "FVG mitigated (invalidated) — no valid entry"
        elif direction == "short" and not entry < stop:
            reject = "geometry: entry not below manipulation extreme (A8)"
        elif direction == "long" and not entry > stop:
            reject = "geometry: entry not above manipulation extreme (A8)"
        elif target is None:
            reject = "no opposing active-ERL liquidity target (B4)"
        elif rr < C.MIN_RR:
            reject = f"RR {rr} < MIN_RR {C.MIN_RR} to real liquidity (B4)"
        actionable = reject == ""

        deps = [f.id]
        if mss_ident:
            deps.append(mss_ident)
        deps.append(sweep.id)
        if dr_id:
            deps.append(dr_id)
        if target_pool is not None:
            deps.append(ids.pool_id(target_pool))

        entry_tf = f.tf or structure_tf
        tf_note = (f" [structure {structure_tf} · entry refined on {entry_tf}]"
                   if entry_tf and entry_tf != structure_tf else f" [{structure_tf}]" if structure_tf else "")
        why = (f"{direction.upper()} from {f.direction} FVG CE {entry:g} (B8){tf_note}; stop at "
               f"manipulation extreme {stop:g} (A8); "
               + (f"target next opposing liquidity {target:g}; RR {rr} to real liquidity"
                  if target is not None else "no opposing liquidity target")
               + (". ACTIONABLE." if actionable else f". REJECTED: {reject}."))
        out.append(Setup(
            id=ids.setup_id(f.id), direction=direction, entry=float(entry), stop=float(stop),
            target=(float(target) if target is not None else None), rr=rr, risk=round(risk, 2),
            reward=round(reward, 2), actionable=actionable, reject_reason=reject, reason=why,
            depends_on=tuple(deps), structure_tf=structure_tf, entry_tf=entry_tf,
        ))
    return out


def recommend(ranked_setups) -> Recommendation:
    """Top ACTIONABLE setup (ranked list of Ranked[Setup]) → LONG/SHORT; else NO-TRADE."""
    for r in ranked_setups:
        s = r.item
        if s.actionable:
            return Recommendation(
                decision="LONG" if s.direction == "long" else "SHORT", setup=s,
                reason=f"Top actionable setup #{r.rank}: {s.reason}", depends_on=s.depends_on)
    n = len(ranked_setups)
    if n == 0:
        return Recommendation("NO-TRADE", None, "No setup candidates at this cursor.", ())
    reasons = "; ".join(f"{r.item.id}: {r.item.reject_reason}" for r in ranked_setups[:5])
    return Recommendation("NO-TRADE", None,
                          f"{n} setup candidate(s), none actionable. Reasons: {reasons}", ())
