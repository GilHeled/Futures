"""Pluggable ICT EXECUTION / ENTRY MODELS for v2.

The execution stage is PLUGGABLE: each model is a separate, named detector that turns the structural
context (a manipulation → displacement → MSS chain in a direction) into an ENTRY (a reference price +
an invalidation level + a lifecycle). Every candidate is tagged with the `entry_model` that produced
it. The engine stays model-agnostic (no `if model ==`); adding a model is a registry entry, never an
engine change. See `docs/ENTRY_MODEL_API.md` for the frozen contract.

SCOPE = THIS COURSE. v2 implements the course methodology (`ict_live/docs/METHODOLOGY_SPEC.md`).

EXECUTION CONFIRMATION = the intraday MARKET-STRUCTURE reversal (Lessons 15 & 16), verified against the
RAW course slides (2026-09-03): Lesson 15 defines a confirmed reversal as the STRUCTURE sequence itself
(long: Low→High→Higher-Low→Higher-High; short: High→Low→Lower-High→Lower-Low), and Lesson 16 (Power of
3) says after the manipulation "we look for the change of direction / the intraday trend change to trade
in the correct direction." So TWO execution models are valid:
  • `structure` — the confirmed structural reversal IS the entry (no FVG required). This is the course's
    core entry basis; it catches reversals that never retrace.
  • `fvg` — a Fair Value Gap is a CONTEXTUAL PD array (Lesson 12: support/resistance where price returns,
    marked on 5m/1m as an entry/exit refinement). It is an OPTIONAL entry refinement, NOT mandatory —
    correcting an earlier mis-hardening ("no FVG retrace = no trade") that inverted the course emphasis.
Order Blocks / Breakers / Mitigation Blocks (broader-ICT constructs the course does not teach) stay out
of scope. New models are added ONLY when the course defines them. v1 is imported nowhere here; v1 frozen.
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
    # structure-confirmation entry: LIVE the moment the reversal confirms (no retrace step)
    "structure": {"vocab": ["forming", "confirmed"],
                  "map": {"forming": "waiting", "confirmed": "valid"}},
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
    "structure": {
        "implemented": True, "detect": None,   # set below (structure_entries)
        "desc": "Market-structure reversal — the confirmed MSS (Low→High→HL→HH long / High→Low→LH→LL short) "
                "IS the entry; no FVG required (Lessons 15/16). Fires on the direction change, LIVE at once.",
    },
    "fvg": {
        "implemented": True, "detect": None,   # FVG entries come from v1's frozen detector (see pipeline)
        "desc": "Fair Value Gap — 3-candle imbalance; entry at CE (50%). A CONTEXTUAL PD array / optional "
                "entry refinement (Lesson 12), no longer a mandatory trigger.",
    },
}

DEFAULT_MODELS: tuple[str, ...] = ("structure", "fvg")   # confirmed reversal OR an FVG-retrace refinement

# --- FVG detector: adapt v1's frozen FVG objects into the common Entry contract ------------------
_FVG_STATUS = {"unfilled": "waiting", "touched": "valid", "mitigated": "mitigated"}

# [RES:fvg_tiebreak] — how to pick between multiple valid FVGs on the SAME displacement leg. The course
# defines NO such rule (verified against the raw lessons). This is an EXPLICIT, UNDECIDED research
# choice, NOT course methodology. Current placeholder = "v1_rank" (prefer unmitigated, then largest
# gap, then most recent — v1's `ranked_fvgs`). Pending an evidence-based decision after live use; do
# NOT treat this value as course-sanctioned.
FVG_TIEBREAK_RULE = "v1_rank"


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
    # ---- SELECTION among multiple FVGs on ONE leg — an EXPLICIT, UNDECIDED [RES:fvg_tiebreak] ----
    # The course teaches WHERE the entry FVG sits (premium/discount, Lesson 12) but defines NO rule
    # for choosing between several valid FVGs in the SAME zone on one displacement leg — verified
    # against the raw lessons (2026-08-27): only "very large FVG → drop a TF" and "mark it in the
    # discount", neither a same-leg tie-break. So this is a labelled [RES] placeholder, NOT course
    # methodology: keep v1's ranking (`ms.ranked_fvgs` = status → LARGEST size → most recent) and take
    # the first unmitigated. To be replaced by an EVIDENCE-BASED rule after live observation of the
    # rare (~5% of legs) multi-unmitigated-FVG cases — deliberately not decided today.
    unmit = [f for f in fvgs if getattr(f, "status", "") != "mitigated"]
    pool = unmit or fvgs                                   # prefer unmitigated; else fall back to mitigated
    f = pool[0]                                            # FVG_TIEBREAK_RULE (below): v1 rank, first unmitigated
    d = "short" if f.direction == "bearish" else "long"
    inval = f.bottom if d == "long" else f.top
    e = Entry(model="fvg", direction=d, ref=f.ce, invalidation=inval,
              lifecycle=_FVG_STATUS.get(getattr(f, "status", "unfilled"), "waiting"),
              id=getattr(f, "id", ""), origin_index=getattr(f, "formed_index", -1), source=f)
    e.tiebreak_n = len(unmit)     # how many unmitigated FVGs shared this leg (>1 ⇒ the [RES] tie-break was exercised)
    return [e]


REGISTRY["fvg"]["detect"] = fvg_entries


# --- structure-confirmation detector: the confirmed reversal itself is the entry (no FVG) ----------
def structure_entries(disp, mss, ms, direction, bars=None) -> list:
    """STRUCTURE-CONFIRMATION entry (Lessons 15 & 16): the confirmed intraday market-structure reversal
    IS the entry — no FVG retrace required. Fires once the leg's MSS is CONFIRMED (a body close beyond
    the last opposing structural swing = the Higher-High that completes Low→High→HL→HH for a long, or the
    Lower-Low completing High→Low→LH→LL for a short). Entry = the confirmation close (Lesson 16: "trade
    the direction change"); invalidation = the manipulation extreme (the displacement origin). The entry
    is LIVE the moment structure confirms, so a non-retracing reversal is still caught.

    [RES:structure_entry_ref] — using the confirmation close as the fill price is a transparent, chart-
    reviewable choice; the course teaches "enter on the direction change" without a tick-precise price.
    Refine after live observation, not by optimization."""
    if mss is None or getattr(mss, "state", "") != "confirmed":
        return []
    want = "bullish" if direction == "long" else "bearish"
    if getattr(mss, "direction", "") != want:
        return []
    ci = getattr(mss, "confirm_index", None)
    if ci is None or not bars or not (0 <= ci < len(bars)):
        return []
    ref = float(bars[ci].close)
    manip = float(getattr(disp, "start_price", ref))          # manipulation extreme = displacement origin
    sid = f"STRUCT-{getattr(disp, 'id', '')}-{ci}"            # unique per displacement leg (no id collisions)
    return [Entry(model="structure", direction=direction, ref=ref, invalidation=manip,
                  lifecycle="confirmed", id=sid, origin_index=ci, source=None)]


REGISTRY["structure"]["detect"] = structure_entries


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
