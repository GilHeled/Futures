"""Cross-timeframe confluence helpers over v1 objects (no v1 code modified).

HISTORY: this module used to expose a `gate_setup` that VETOED a setup on HTF bias mismatch, wrong
premium/discount, or a missing objective. That veto has been REMOVED — HTF bias and premium/discount
are QUALITY / context (§1/§17: labels, not vetoes), and are surfaced by the pipeline's semantic layer
(structure / quality / course-filters / recommendation, see `ict_v2/recommend.py`). What remains here
is the pure computation of the liquidity DRAW (the target), which the pipeline uses as the objective.

DRAW SELECTION IS ERL/IRL CLASS-AWARE (Lesson 10, user decision 2026-08-28). The course gives a
specific relationship, not a "nearest level by price" rule: when EXTERNAL liquidity (ERL) is taken,
price seeks INTERNAL liquidity (IRL — e.g. an FVG) to rebalance; when the internal imbalance is
rebalanced/closed, price seeks EXTERNAL liquidity (new price discovery). So `next_draw` decides the
liquidity CLASS first, then picks the objective INSIDE that class. It never compares ERL and IRL by
raw distance.
"""
from __future__ import annotations

from dataclasses import dataclass


def liquidity_objective(context, direction: str):
    """The HTF liquidity pool a setup in `direction` is drawing toward (target), or None.
    long → buy-side liquidity (a swing HIGH pool) above equilibrium; short → sell-side (a LOW) below."""
    want = "high" if direction == "long" else "low"
    pools = [p for p in (context.liquidity or []) if getattr(p, "kind", None) == want]
    if not pools:
        return None
    ce = context.dealing_range.ce if context.dealing_range is not None else None
    if direction == "long":
        above = [p for p in pools if ce is None or p.price > ce]
        return min(above, key=lambda p: p.price) if above else max(pools, key=lambda p: p.price)
    below = [p for p in pools if ce is None or p.price < ce]
    return max(below, key=lambda p: p.price) if below else min(pools, key=lambda p: p.price)


@dataclass
class DrawObjective:
    """The chosen draw (target) plus WHICH liquidity class it belongs to (Lesson 10). Exposes `.kind`
    and `.price` so every existing consumer (assemble / pipeline / snapshot) treats it exactly like a
    SwingPool; `.klass`/`.array_kind`/`.basis` add the class-aware provenance for the dashboard."""
    klass: str                 # "ERL" | "IRL" — the class the course says price seeks next
    kind: str                  # "high" (buy-side draw) | "low" (sell-side draw) — uniform w/ SwingPool
    price: float
    array_kind: str = "swing"  # "swing" (ERL pool) | "fvg" | "nwog" | "org" — the object giving the draw
    source: object = None
    basis: str = ""            # human WHY (which Lesson-10 branch fired)
    tiebreak_n: int = 0        # >1 ⇒ several IRL candidates; the pick is a surfaced [RES:irl_draw_tiebreak]


def _internal_draw(context, direction: str, internal_arrays):
    """The IRL objective (Lesson 10): when no external liquidity remains to seek, price rebalances an
    INTERNAL imbalance. Candidates = UNFILLED PD arrays on the DRAW side of the dealing range for the
    direction (premium for a long, discount for a short). WITHIN the IRL class the course gives no
    tie-break, so among several we take the imbalance NEAREST equilibrium (the first price reaches on
    the way out) and SURFACE the count as [RES:irl_draw_tiebreak] — a labelled, reviewable choice, not
    a silent 'nearest across everything' heuristic (that mixing was explicitly rejected)."""
    dr = context.dealing_range if context is not None else None
    ce = dr.ce if dr is not None else None
    if ce is None:
        return None
    want_premium = (direction == "long")                     # long draws UP into premium; short DOWN
    cands = [a for a in (internal_arrays or [])
             if getattr(a, "status", "") != "mitigated"
             and ((a.ce > ce) if want_premium else (a.ce < ce))]
    if not cands:
        return None
    pick = min(cands, key=lambda a: abs(a.ce - ce))          # nearest equilibrium = first rebalance target
    return DrawObjective(klass="IRL", kind=("high" if want_premium else "low"), price=pick.ce,
                         array_kind=getattr(pick, "kind", "fvg"), source=pick, tiebreak_n=len(cands),
                         basis="no unswept external pool — price seeks to rebalance an internal "
                               "imbalance (IRL/FVG) (Lesson 10)")


def next_draw(context, direction: str, *, internal_arrays=None):
    """The draw (target) a `direction` setup seeks — CLASS FIRST (Lesson 10), then the objective inside
    that class. A sweep-anchored reversal takes near-side ERL (the manipulation) and, after the
    internal rebalance (the entry), the REAL MOVE seeks the OPPOSING EXTERNAL liquidity — the course's
    terminal draw / new price discovery (Lesson 10 p3). So the class is ERL whenever an opposing
    UNSWEPT external pool exists; only when none remains does price seek to rebalance an internal
    imbalance (IRL/FVG). Returns a DrawObjective, or None if neither class yields a draw."""
    erl = liquidity_objective(context, direction)
    if erl is not None:
        return DrawObjective(klass="ERL", kind=erl.kind, price=erl.price, array_kind="swing",
                             source=erl,
                             basis="opposing external liquidity unswept — real move seeks ERL (Lesson 10)")
    return _internal_draw(context, direction, internal_arrays)
