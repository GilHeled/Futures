"""LIQUIDITY OBJECTIVES — the one general abstraction the Scenario Layer is built on (user decision
2026-08-28).

The course does NOT split "price liquidity" (BSL/SSL, EQH/EQL) from "PD-array liquidity" (FVG, NWOG,
ORG, fib). They are ALL just liquidity objectives price may be SEEKING (a draw) or REACTING from,
depending on context. So the engine collects every meaningful objective into ONE typed list, and the
Scenario Layer ranks them together against the H4/H1 context. Adding a new PD array the course teaches
later is then just another `kind` here — never a redesign.

Each objective carries: `kind` (swing/eqhl/fvg/nwog/org/fib), `tf`, `side` (buy/sell), `price`
(+optional zone bounds), `liquidity_class` (ERL/IRL vs the dealing range, Lesson 10), `strength`
(a transparent ranking scalar), `role` (draw/reaction/entry — context-assigned via the SAME rule as
`pdarrays.role_of`), lifecycle `status`, and a human `label`. v1 stays the detector; this only adapts.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ict_v2 import pdarrays as PD


# ---- ranking weights ([NEC], NOT course numbers) ---------------------------------------------
# The course says only "higher timeframe = stronger" (Lesson 6/11) and gives no scale. These are
# transparent, tunable engineering weights for RANKING objectives — never gates. Documented so the
# ranking is auditable, not hidden.
_TF_WEIGHT = {"W": 1.0, "D": 0.9, "4H": 0.8, "1H": 0.6, "15m": 0.45, "5m": 0.3, "1m": 0.2}
_KIND_WEIGHT = {"swing": 1.0, "eqhl": 1.0, "fvg": 0.8, "nwog": 0.7, "org": 0.7, "fib": 0.5}
_LIFECYCLE_WEIGHT = {"unswept": 1.0, "unfilled": 1.0, "touched": 0.6,
                     "open": 1.0, "swept": 0.0, "mitigated": 0.0, "closed": 0.4, "": 0.8}

_OBJECTIVE_KINDS = ("swing", "eqhl", "fvg", "nwog", "org", "fib")


def _tf_weight(tf: str) -> float:
    return _TF_WEIGHT.get(tf, max((_TF_WEIGHT.get(k, 0.0) for k in _TF_WEIGHT if k == tf), default=0.5))


@dataclass
class LiquidityObjective:
    """One liquidity objective of ANY kind — a level/zone price may seek (draw) or react from."""
    kind: str                       # swing | eqhl | fvg | nwog | org | fib
    tf: str
    side: str                       # "high" (buy-side/BSL) | "low" (sell-side/SSL) | "" (non-directional)
    price: float                    # the reference level (pool price / FVG CE / gap mid / fib price)
    top: float | None = None        # zone bounds for zone objectives (FVG/NWOG/ORG); None for point levels
    bottom: float | None = None
    liquidity_class: str = ""       # "ERL" | "IRL" (external vs internal vs the dealing range — Lesson 10)
    strength: float = 0.0           # transparent ranking scalar (tf × kind × lifecycle) — never a gate
    role: str = ""                  # draw | reaction | entry | inactive (context-assigned)
    status: str = ""                # lifecycle: unswept/swept | unfilled/touched/mitigated | open/closed
    label: str = ""                 # human ("BSL","SSL","FVG","NWOG","ORG","fib 0.62 (OTE)", …)
    role_basis: dict = field(default_factory=dict)
    source: object = None           # underlying v1 object (audit)

    def to_dict(self) -> dict:
        def px(x):
            return None if x is None else round(float(x), 2)
        return {"kind": self.kind, "tf": self.tf, "side": self.side, "price": px(self.price),
                "top": px(self.top), "bottom": px(self.bottom), "liquidity_class": self.liquidity_class,
                "strength": round(self.strength, 3), "role": self.role, "status": self.status,
                "label": self.label, "role_basis": dict(self.role_basis)}


def _strength(kind: str, tf: str, status: str) -> float:
    return round(_tf_weight(tf) * _KIND_WEIGHT.get(kind, 0.5)
                 * _LIFECYCLE_WEIGHT.get(status, 0.8), 3)


def _assign_role(obj: LiquidityObjective, direction: str, zone: str | None) -> None:
    """Assign the objective's contextual role using the SAME rule as pdarrays.role_of (tf class ×
    dealing-range side × lifecycle), so every objective — swing, fib, FVG, gap — is roled consistently
    and auditably. Records the full trace in `role_basis`."""
    tc = PD.tf_class(obj.tf)
    side = PD._side(zone, direction)
    spent = obj.status in ("swept", "mitigated")
    if spent:
        # a taken swing / mitigated FVG is spent; a closed NWOG/ORG stays a reaction (Lesson 13)
        role = "reaction" if obj.kind in ("nwog", "org") else "inactive"
        rule = ("closed gap kept as S/R (Lesson 13)" if role == "reaction"
                else f"{obj.kind} taken/mitigated — spent")
    elif side == "retrace":
        role = "entry" if tc == "LTF" else "reaction"
        rule = (f"{tc} {obj.kind} on the retracement side → ENTRY (Lesson 12: 5m/1m entry)"
                if role == "entry" else f"{tc} {obj.kind} on the retracement side → REACTION (entry is LTF-only)")
    elif side == "draw":
        role = "draw" if tc in ("HTF", "MTF") else "reaction"
        rule = (f"{tc} {obj.kind} on the draw side → DRAW / objective (Lesson 10/11: price seeks it)"
                if role == "draw" else f"{tc} {obj.kind} on the draw side → REACTION")
    else:
        role = "reaction"
        rule = "no clear dealing-range side (equilibrium / no range) → REACTION"
    obj.role = role
    obj.role_basis = {"tf_class": tc, "dealing_range_position": zone, "liquidity_class": obj.liquidity_class,
                      "side": side, "lifecycle": obj.status, "kind": obj.kind, "rule": rule, "role": role}


# ---- collection: gather EVERY objective from a context, typed + roled ------------------------
def _fib_label(level: float) -> str:
    return {0.5: "fib 0.5 (EQ)", 0.62: "fib 0.62 (OTE)", 0.79: "fib 0.79 (OTE)"}.get(level, f"fib {level:g}")


def collect_objectives(context, *, direction: str | None = None, extra_arrays=None,
                       gaps=None) -> list[LiquidityObjective]:
    """Gather ALL liquidity objectives visible from `context` into one typed, roled, strength-scored
    list — swing pools (BSL/SSL), the context's FVG PD arrays, NWOG/ORG gaps, and the key fib levels
    (0.5/0.62/0.79). EQH/EQL is a KNOWN GAP (clustering tolerance undefined) — intentionally omitted
    until defined; when it is, it becomes one more `kind` here with no redesign.

    `direction` (long/short) is the trade direction the roles are assigned FOR (draw side = premium for
    long); if None, roles are left blank (pure inventory). `extra_arrays` = additional PDArrays (e.g.
    1H FVGs). `gaps` = NWOG/ORG dicts already computed (each may carry top/bottom/mid/closed)."""
    dr = getattr(context, "dealing_range", None)
    zone_of = (lambda p: dr.zone_of(p)) if dr is not None else (lambda p: None)
    erl_irl = getattr(context, "erl_irl", lambda p: None)
    out: list[LiquidityObjective] = []

    def _add(o: LiquidityObjective):
        o.liquidity_class = o.liquidity_class or (erl_irl(o.price) or "")
        o.strength = _strength(o.kind, o.tf, o.status)
        if direction:
            _assign_role(o, direction, zone_of(o.price))
        out.append(o)

    # 1) swing pools — BSL (high) / SSL (low); untaken = a resting draw (Lesson 6/11)
    for p in (getattr(context, "liquidity", None) or []):
        side = getattr(p, "kind", None)
        if side not in ("high", "low"):
            continue
        _add(LiquidityObjective(kind="swing", tf=getattr(context, "tf", ""), side=side,
                                price=float(p.price), status="unswept",
                                label=("BSL" if side == "high" else "SSL"), source=p))

    # 2) FVG PD arrays already roled on the context (draws) + any extra 1H/context FVGs supplied
    seen_fvg = set()
    for arr in list(getattr(context, "draws", []) or []) + list(extra_arrays or []):
        key = (round(arr.ce, 2), arr.tf)
        if key in seen_fvg:
            continue
        seen_fvg.add(key)
        side = "high" if arr.polarity == "bullish" else "low" if arr.polarity == "bearish" else ""
        _add(LiquidityObjective(kind="fvg", tf=arr.tf, side=side, price=float(arr.ce),
                                top=float(arr.top), bottom=float(arr.bottom), status=arr.status,
                                label="FVG", source=arr.source if arr.source is not None else arr))

    # 3) NWOG / ORG gaps (Lessons 13/14) — S/R + magnet/target
    for g in (gaps or []):
        kind = g.get("_kind", "nwog")
        _add(LiquidityObjective(kind=kind, tf=g.get("tf", "W" if kind == "nwog" else "D"), side="",
                                price=float(g["mid"]), top=float(g["top"]), bottom=float(g["bottom"]),
                                status=("closed" if g.get("closed") else "open"),
                                label=kind.upper(), source=g))

    # 4) key fib levels (Lesson 8): 0.5 (EQ) / 0.62 / 0.79 (OTE) — magnets / reaction levels
    if dr is not None:
        ce = dr.ce
        for lvl in getattr(context, "fib_levels", lambda: [])():
            if lvl["level"] in (0.5, 0.62, 0.79):
                side = "high" if lvl["price"] >= ce else "low"
                _add(LiquidityObjective(kind="fib", tf=getattr(context, "tf", ""), side=side,
                                        price=float(lvl["price"]), status="", label=_fib_label(lvl["level"]),
                                        source=lvl))
    return out


def draws(objectives) -> list[LiquidityObjective]:
    """The objectives currently acting as DRAWS (targets), strongest first — the Scenario Layer's raw
    material."""
    return sorted([o for o in objectives if o.role == "draw"], key=lambda o: o.strength, reverse=True)


# Which kinds can be a DRAW (a target price seeks). Fib 0.5/OTE are retracement/reaction references, NOT
# draws (the course targets liquidity + imbalance; fib EXTENSIONS would be draws but their projection is
# the deferred, ambiguous item). Extensible: a future course PD array just joins this set.
_DRAW_KINDS = ("swing", "eqhl", "fvg", "nwog", "org")
_ACTIVE_STATUS = ("unswept", "unfilled", "open", "touched", "")   # not swept/mitigated/closed-spent


def viable_targets(objectives) -> list[LiquidityObjective]:
    """Objectives that can be a scenario DRAW — a liquidity-kind objective, still active (untaken), on a
    directional side, on an HTF/MTF timeframe (a draw is set on the higher timeframes; LTF objects are
    execution, Lesson 11/12). Direction-agnostic: a high-side target implies a long thesis, a low-side a
    short — so scenarios for BOTH directions come from one call, independent of any single-direction role."""
    out = []
    for o in objectives:
        if (o.kind in _DRAW_KINDS and o.side in ("high", "low")
                and o.status in _ACTIVE_STATUS and PD.tf_class(o.tf) in ("HTF", "MTF")):
            out.append(o)
    return out
