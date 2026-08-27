"""Pluggable ICT EXECUTION / ENTRY MODELS for v2.

The execution stage is PLUGGABLE: each model is a separate, named detector that turns the structural
context (a manipulation → displacement → MSS chain in a direction) into an ENTRY (a reference price +
an invalidation level + a lifecycle). Every candidate is tagged with the `entry_model` that produced
it. The engine stays model-agnostic (no `if model ==`); adding a model is a registry entry, never an
engine change. See `docs/ENTRY_MODEL_API.md` for the frozen contract.

SCOPE = THIS COURSE, NOT ICT IN GENERAL. v2 faithfully implements the specific course methodology
captured in `ict_live/docs/METHODOLOGY_SPEC.md` (+ `ict_faithful/SPEC.md`). In that methodology the
entry PD array is the **Fair Value Gap** (§13/§14: sweep → displacement → confirmed MSS → same-leg
FVG → retrace → entry); the only other PD-arrays named are NWOG/ORG, used as context/targets. The
course does NOT define Order Blocks, Breakers, or Mitigation Blocks as execution models — those belong
to broader ICT teaching, not to this course — so they are intentionally NOT implemented here. FVG is
the sole execution model. Additional models are added ONLY when authoritative course material defines
them; the framework exists so that is a plugin, not a rebuild. v1 is imported nowhere here; v1 frozen.
"""
from __future__ import annotations

from dataclasses import dataclass


# TWO-LEVEL STATE. The COMMON state is all the engine ever looks at; the model-specific LIFECYCLE
# sub-state is for the dashboard only.
COMMON_STATES = ("waiting", "valid", "rejected", "completed")
#   waiting    — the entry object exists but is not yet usable (still forming / awaiting its trigger)
#   valid      — the entry is live/usable (an order can rest here now)
#   rejected   — the entry never became valid (structural fault: bad geometry / invalidated early)
#   completed  — the entry was valid and then played out / was consumed (e.g. an FVG mitigated)

# Each model's own lifecycle vocabulary + how each sub-state maps to the ONE common state above.
# Only the course's execution model (FVG) is defined. A future course-defined model registers its own
# lifecycle here — the two-level-state mechanism is generic and does not privilege FVG.
LIFECYCLE = {
    "fvg": {"vocab": ["waiting", "valid", "mitigated"],
            "map": {"waiting": "waiting", "valid": "valid", "mitigated": "completed"}},
}


def common_state(model: str, lifecycle: str) -> str:
    """Map a model's lifecycle sub-state to the ONE common state the engine uses."""
    return LIFECYCLE.get(model, {}).get("map", {}).get(lifecycle, "waiting")


@dataclass
class Entry:
    """THE COMMON CONTRACT every execution model implements. The engine + dashboard consume this
    generic object without knowing the model. `state` is the common state (all the engine reads);
    `lifecycle` is the model-specific sub-state (dashboard only), derived → common if `state` omitted."""
    model: str                    # registry key (the course's entry model is "fvg")
    direction: str                # "long" | "short"
    ref: float                    # REFERENCE ENTRY PRICE (where the order rests)
    invalidation: float           # INVALIDATION LEVEL (price beyond which the entry object is void)
    lifecycle: str = ""           # MODEL-SPECIFIC sub-state (e.g. fvg: waiting|valid|mitigated)
    state: str = ""               # COMMON state (waiting|valid|rejected|completed) — derived if empty
    quality: "float|None" = None  # CONFIDENCE / QUALITY 0..1 if the model provides one, else None
    reason: str = ""              # REASON IF REJECTED / not usable (empty when fine)
    origin_index: int = -1        # bar index where the entry object formed
    id: str = ""
    source: object = None         # underlying detector object (audit/deps); consumers ignore it

    def __post_init__(self):
        if not self.state:                                # derive the common state from the lifecycle
            self.state = common_state(self.model, self.lifecycle)

    def to_dict(self) -> dict:
        def px(x):
            return None if x is None else round(float(x), 2)
        return {"model": self.model, "direction": self.direction, "ref": px(self.ref),
                "invalidation": px(self.invalidation), "state": self.state, "lifecycle": self.lifecycle,
                "quality": self.quality, "reason": self.reason}


# The registry of execution models. Only the course's entry array (FVG) is defined. `implemented`
# flags whether a real detector exists; `detect` is the detector. A model is added here ONLY when
# authoritative course material defines it (Order Block / Breaker / Mitigation Block / IFVG / IOFED
# are broader-ICT constructs the captured course does NOT teach, so they are deliberately absent).
REGISTRY: dict[str, dict] = {
    "fvg": {
        "implemented": True, "detect": None,   # FVG entries come from v1's frozen detector (see pipeline)
        "desc": "Fair Value Gap — 3-candle imbalance; entry at CE (50%). The course's entry PD array.",
    },
}

DEFAULT_MODELS: tuple[str, ...] = ("fvg",)          # FVG is the course's sole execution model

# --- FVG detector: adapt v1's frozen FVG objects into the common Entry contract ------------------
_FVG_STATUS = {"unfilled": "waiting", "touched": "valid", "mitigated": "mitigated"}


def detect(model: str, disp, mss, ms, direction, bars) -> list:
    """Ask a model for its entries on one displacement leg — the engine calls THIS, never a
    model-specific function. `bars` is the raw OHLC window (same cursor) every model receives, so a
    from-scratch, candle-body model (Order Block, Breaker, …) can read candles v1 never pre-computed;
    a model that sources pre-detected objects off `ms` (FVG) simply ignores it. Returns [] for models
    without a detector (planned)."""
    fn = REGISTRY.get(model, {}).get("detect")
    return fn(disp, mss, ms, direction, bars) if fn else []


def validate(model: str, entry: Entry, geom: dict, context) -> tuple:
    """Optional MODEL-SPECIFIC validation beyond the universal geometry checks in assemble(). Returns
    (ok, reason). Default (no validator) = (True, "")."""
    fn = REGISTRY.get(model, {}).get("validate")
    return fn(entry, geom, context) if fn else (True, "")


def fvg_entries(disp, mss, ms, direction, bars=None) -> list:
    """The FVG entry(ies) on one displacement leg, as generic Entry objects. Sources v1's frozen FVG
    detector (`ms.ranked_fvgs`, linked to the displacement); prefers an unmitigated gap. Entry ref =
    the gap CE (B8); invalidation = the far edge of the gap. `bars` is unused here — FVGs are already
    pre-computed on `ms` — but is accepted so every detector shares the one v1.1 signature."""
    fvgs = [r.item for r in ms.ranked_fvgs
            if getattr(r.item, "depends_on", None) and r.item.depends_on[0] == disp.id]
    if not fvgs:
        return []
    fvgs.sort(key=lambda f: 0 if getattr(f, "status", "") != "mitigated" else 1)   # prefer unmitigated
    f = fvgs[0]
    d = "short" if f.direction == "bearish" else "long"
    inval = f.bottom if d == "long" else f.top
    return [Entry(model="fvg", direction=d, ref=f.ce, invalidation=inval,
                  lifecycle=_FVG_STATUS.get(getattr(f, "status", "unfilled"), "waiting"),
                  id=getattr(f, "id", ""), origin_index=getattr(f, "formed_index", -1), source=f)]


REGISTRY["fvg"]["detect"] = fvg_entries


def assemble(entry: Entry, sweep_extreme: float, active_erl, min_stop=None) -> dict:
    """Uniform geometry for ANY entry model — the engine calls this without knowing the model:
    entry = the model's reference; stop = the manipulation extreme; target = nearest opposing active
    ERL (the draw). Returns {entry, stop, target, rr, objective, reject}. `reject` is a STRUCTURAL
    invalidation (mitigated / degenerate stop / bad geometry / no target); RR is a quality metric, NOT
    a gate here (v2 separates valid-setup from good-trade)."""
    d, E, S = entry.direction, entry.ref, sweep_extreme
    if d == "long":
        cands = [p for p in (active_erl or []) if getattr(p, "kind", None) == "high" and p.price > E]
        obj = min(cands, key=lambda p: p.price) if cands else None
    else:
        cands = [p for p in (active_erl or []) if getattr(p, "kind", None) == "low" and p.price < E]
        obj = max(cands, key=lambda p: p.price) if cands else None
    target = getattr(obj, "price", None) if obj is not None else None
    risk = abs(S - E)
    reward = abs(E - target) if target is not None else 0.0
    rr = round(reward / risk, 2) if risk > 0 else None
    reject = ""
    if entry.state in ("completed", "rejected"):          # engine reads only the COMMON state
        reject = f"entry {entry.lifecycle or entry.state} — no valid entry"
    elif min_stop and risk < min_stop:
        reject = f"degenerate stop — risk {round(risk, 2)} < execution floor {min_stop:g}"
    elif (d == "long" and not E > S) or (d == "short" and not E < S):
        reject = "geometry: entry not beyond the manipulation extreme"
    elif target is None:
        reject = "no opposing active-ERL liquidity target"
    return {"entry": E, "stop": S, "target": target, "rr": rr, "objective": obj, "reject": reject}


def resolve(names) -> tuple[str, ...]:
    """Filter requested model names to the ones that are IMPLEMENTED (planned/unknown ones are
    silently dropped — enabling them is inert). Always keeps at least FVG so the cascade still runs."""
    if not names:
        return DEFAULT_MODELS
    out = [n for n in names if REGISTRY.get(n, {}).get("implemented")]
    return tuple(out) if out else DEFAULT_MODELS


def catalog() -> dict:
    """Registry summary for the dashboard: which models are implemented vs planned, their description,
    and their model-specific lifecycle (for display)."""
    return {n: {"implemented": e["implemented"], "desc": e["desc"],
                "lifecycle": LIFECYCLE.get(n, {}).get("vocab", [])} for n, e in REGISTRY.items()}
