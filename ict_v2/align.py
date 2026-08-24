"""Cross-timeframe confluence — the HTF context GATE applied to MTF setups.

An MTF setup passes only when it agrees with the higher-timeframe context on all three ICT axes:
  1. directional bias   — setup direction == HTF bias
  2. premium/discount   — longs in HTF discount, shorts in HTF premium (equilibrium tolerated)
  3. liquidity objective — an HTF draw exists in the setup's direction (BSL above for longs / SSL below)

Pure functions over v1 objects (Setup, DealingRange, SwingPool); no v1 code is modified.
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


def gate_setup(setup, context):
    """Return (passed: bool, reasons: list[str], objective). `reasons` is the list of gate FAILURES
    (empty ⇒ passed). `objective` is the HTF liquidity draw for the setup direction (may be None)."""
    reasons = []
    d = setup.direction
    # 1. directional bias
    if context.bias == "neutral":
        reasons.append("HTF bias is neutral")
    elif d != context.bias:
        reasons.append(f"direction {d} != HTF bias {context.bias}")
    # 2. premium/discount location of the entry
    zone = context.zone(setup.entry)
    if zone is not None:
        want = "discount" if d == "long" else "premium"
        if zone not in (want, "equilibrium"):
            reasons.append(f"entry in HTF {zone}, want {want}")
    # 3. liquidity objective in the setup direction
    objective = liquidity_objective(context, d)
    if objective is None:
        reasons.append("no HTF liquidity objective in setup direction")
    return (not reasons), reasons, objective
