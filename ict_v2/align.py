"""Cross-timeframe confluence helpers over v1 objects (no v1 code modified).

HISTORY: this module used to expose a `gate_setup` that VETOED a setup on HTF bias mismatch, wrong
premium/discount, or a missing objective. That veto has been REMOVED — HTF bias and premium/discount
are QUALITY / context (§1/§17: labels, not vetoes), and are surfaced by the pipeline's semantic layer
(structure / quality / course-filters / recommendation, see `ict_v2/recommend.py`). What remains here
is the pure computation of the liquidity DRAW (the target), which the pipeline uses as the objective.
"""
from __future__ import annotations


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
