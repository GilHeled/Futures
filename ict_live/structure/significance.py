"""Objective STRUCTURAL swing classification (experimental dev aid; nothing frozen).

Reframed per the methodology owner: "significant" is NOT a permanent attribute of a swing. The
methodology is about LIQUIDITY — a swing becomes relevant when liquidity develops around it or
later price action makes it the draw. So this module now produces only the OBJECTIVE structural
skeleton; whether a structural swing is an active ERL is decided by the liquidity layer
(`structure/swing_liquidity.py`), contextually, not here.

Tiers (each raw fractal pivot gets the HIGHEST tier it reaches):
  * structural — candidates reduced to the alternating HH/HL/LH/LL skeleton (a run of same-side
                 pivots collapses to its extreme).
  * rejected   — a candidate collapsed out of the skeleton (minor noise).

Diagnostic flags (NOT verdicts, NOT frozen): `dominant` (a degree-2 structural extreme — higher
than adjacent structural highs / lower than adjacent structural lows), `broken` (a later body
close went beyond it), `protected` (the standing unbroken extreme). The liquidity layer MAY use
these as inputs, but a swing's importance is not fixed here.

`config.SIGNIFICANT_SWING_MAGNITUDE` remains a hard sentinel and the "significant swing" question
is deliberately left OPEN — we build on objective structure + contextual liquidity instead.
"""
from __future__ import annotations

from dataclasses import dataclass

from ict_live.market.bar import Bar
from ict_live.structure.swings import Swing

TIERS = ("rejected", "structural")


@dataclass(frozen=True)
class ClassifiedSwing:
    swing: Swing
    tier: str            # "rejected" | "structural"
    dominant: bool       # degree-2 structural extreme (diagnostic only)
    broken: bool         # a later body close went beyond this pivot (diagnostic)
    protected: bool      # current standing (unbroken) extreme of its side (diagnostic)
    reason: str = ""     # human-readable WHY (for the visual audit / decision log)


def _more_extreme(a: Swing, b: Swing) -> bool:
    return a.price > b.price if a.kind == "high" else a.price < b.price


def classify(candidates: list[Swing], bars: list[Bar]) -> list[ClassifiedSwing]:
    """Classify every candidate pivot into the objective structural skeleton. Causal: any
    look-back at `bars` only ever uses bars AFTER the pivot."""
    ordered = sorted(candidates, key=lambda s: (s.index, 0 if s.kind == "high" else 1))

    # --- structural skeleton: collapse runs of same-side pivots to their extreme ---
    skeleton: list[Swing] = []
    in_skeleton: set[tuple[str, int]] = set()
    for s in ordered:
        if skeleton and skeleton[-1].kind == s.kind:
            if _more_extreme(s, skeleton[-1]):
                in_skeleton.discard((skeleton[-1].kind, skeleton[-1].index))
                skeleton[-1] = s
                in_skeleton.add((s.kind, s.index))
        else:
            skeleton.append(s)
            in_skeleton.add((s.kind, s.index))

    # --- diagnostics (not verdicts) ---
    def _degree2(seq: list[Swing], is_high: bool) -> set[tuple[str, int]]:
        keys = set()
        for k, s in enumerate(seq):
            left = k == 0 or (s.price > seq[k - 1].price if is_high else s.price < seq[k - 1].price)
            right = k == len(seq) - 1 or (s.price > seq[k + 1].price if is_high else s.price < seq[k + 1].price)
            if left and right:
                keys.add((s.kind, s.index))
        return keys

    highs = [s for s in skeleton if s.kind == "high"]
    lows = [s for s in skeleton if s.kind == "low"]
    dominant = _degree2(highs, True) | _degree2(lows, False)

    def broken(sw: Swing) -> bool:
        return any((b.close > sw.price) if sw.kind == "high" else (b.close < sw.price)
                   for b in bars[sw.index + 1:])

    broken_map = {(s.kind, s.index): broken(s) for s in skeleton}
    last_hi = max((s for s in highs if not broken_map[(s.kind, s.index)]),
                  key=lambda s: s.index, default=None)
    last_lo = max((s for s in lows if not broken_map[(s.kind, s.index)]),
                  key=lambda s: s.index, default=None)
    protected = {(p.kind, p.index) for p in (last_hi, last_lo) if p}

    out: list[ClassifiedSwing] = []
    for s in ordered:
        key = (s.kind, s.index)
        if key not in in_skeleton:
            out.append(ClassifiedSwing(s, "rejected", False, False, False,
                       reason="collapsed out of the structural skeleton "
                              "(a less-extreme same-side pivot in a run)"))
            continue
        is_dom, is_brk, is_prot = key in dominant, broken_map.get(key, False), key in protected
        bits = ["alternating structural pivot (HH/HL/LH/LL skeleton)"]
        if is_dom:
            bits.append("dominant: degree-2 extreme (beyond adjacent same-kind structural swings)")
        if is_prot:
            bits.append("protected: current standing unbroken extreme of its side")
        if is_brk:
            bits.append("later body close traded beyond it")
        out.append(ClassifiedSwing(s, "structural", is_dom, is_brk, is_prot,
                                   reason=" · ".join(bits)))
    return out


def structural_swings(classified: list[ClassifiedSwing]) -> list[Swing]:
    return [c.swing for c in classified if c.tier == "structural"]


def counts(classified: list[ClassifiedSwing]) -> dict[str, int]:
    c = {t: 0 for t in TIERS}
    for cs in classified:
        c[cs.tier] += 1
    return c
