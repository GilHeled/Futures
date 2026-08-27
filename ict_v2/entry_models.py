"""Pluggable ICT EXECUTION / ENTRY MODELS for v2.

The course teaches several execution models, not just the Fair Value Gap. This module makes the
execution stage pluggable: each model is a separate, named detector that turns the structural context
(a manipulation → displacement → MSS chain in a direction) into an ENTRY (a reference price + an
invalidation level + a fill status). Every candidate is tagged with the `entry_model` that produced
it, so the UI and any later study can tell them apart.

FAITHFULNESS / COMPLETENESS ONLY — no edge claims, no tuning, no PnL comparison. FVG is the working
reference model; the other course models are registered as first-class entries but are OFF until each
is implemented AND verified in its own increment. Enabling a not-yet-implemented model is inert.
v1 is imported nowhere here; v1 stays frozen.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Entry:
    """A single entry produced by an execution model, within one displacement leg."""
    model: str                 # registry key: "fvg" | "order_block" | ...
    direction: str             # "long" | "short"
    ref: float                 # the entry reference price (where the order rests)
    status: str                # "unfilled" | "touched" | "mitigated"
    invalidation: float        # price beyond which the entry object is void
    origin_index: int = -1     # bar index where the entry object formed
    id: str = ""
    why: str = ""


# The course execution models, as first-class registry entries. `implemented` flags whether a real,
# verified detector exists yet; `detect` is the detector (None until implemented). Descriptions are
# kept faithful to how the course frames each model.
REGISTRY: dict[str, dict] = {
    "fvg": {
        "implemented": True, "detect": None,   # FVG entries come from v1's frozen detector (see pipeline)
        "desc": "Fair Value Gap — 3-candle imbalance; entry at CE (50%). The B8 default entry.",
    },
    "order_block": {
        "implemented": False, "detect": None,
        "desc": "Order Block — last opposing-close candle before the displacement; entry at its 50%.",
    },
    "breaker": {
        "implemented": False, "detect": None,
        "desc": "Breaker Block — a failed order block that price flips into support/resistance.",
    },
    "mitigation_block": {
        "implemented": False, "detect": None,
        "desc": "Mitigation Block — origin block of a move that failed to make a new extreme.",
    },
    "ifvg": {
        "implemented": False, "detect": None,
        "desc": "Inversion FVG — a mitigated FVG that inverts and acts as the opposite S/R.",
    },
    "iofed": {
        "implemented": False, "detect": None,
        "desc": "IOFED — Institutional Order Flow Entry Drill (entry on the first FVG after a raid).",
    },
}

DEFAULT_MODELS: tuple[str, ...] = ("fvg",)          # only FVG is on by default


def resolve(names) -> tuple[str, ...]:
    """Filter requested model names to the ones that are IMPLEMENTED (planned/unknown ones are
    silently dropped — enabling them is inert). Always keeps at least FVG so the cascade still runs."""
    if not names:
        return DEFAULT_MODELS
    out = [n for n in names if REGISTRY.get(n, {}).get("implemented")]
    return tuple(out) if out else DEFAULT_MODELS


def catalog() -> dict:
    """Registry summary for the dashboard: which models are implemented vs planned, with descriptions."""
    return {n: {"implemented": e["implemented"], "desc": e["desc"]} for n, e in REGISTRY.items()}
