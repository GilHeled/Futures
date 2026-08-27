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


# TWO-LEVEL STATE. The COMMON state is all the engine ever looks at; the model-specific LIFECYCLE
# sub-state is for the dashboard only.
COMMON_STATES = ("waiting", "valid", "rejected", "completed")
#   waiting    — the entry object exists but is not yet usable (still forming / awaiting its trigger)
#   valid      — the entry is live/usable (an order can rest here now)
#   rejected   — the entry never became valid (structural fault: bad geometry / invalidated early)
#   completed  — the entry was valid and then played out / was consumed (e.g. an FVG mitigated)

# Each model's own lifecycle vocabulary + how each sub-state maps to the ONE common state above.
LIFECYCLE = {
    "fvg":              {"vocab": ["waiting", "valid", "mitigated"],
                         "map": {"waiting": "waiting", "valid": "valid", "mitigated": "completed"}},
    "order_block":      {"vocab": ["identified", "awaiting_retest", "validated", "invalidated", "mitigated"],
                         "map": {"identified": "waiting", "awaiting_retest": "waiting",
                                 "validated": "valid", "invalidated": "rejected", "mitigated": "completed"}},
    "breaker":          {"vocab": ["formed", "confirmed", "invalidated", "mitigated"],
                         "map": {"formed": "waiting", "confirmed": "valid",
                                 "invalidated": "rejected", "mitigated": "completed"}},
    "mitigation_block": {"vocab": ["formed", "awaiting_retest", "validated", "invalidated", "mitigated"],
                         "map": {"formed": "waiting", "awaiting_retest": "waiting", "validated": "valid",
                                 "invalidated": "rejected", "mitigated": "completed"}},
    "ifvg":             {"vocab": ["inverted", "confirmed", "invalidated", "mitigated"],
                         "map": {"inverted": "waiting", "confirmed": "valid",
                                 "invalidated": "rejected", "mitigated": "completed"}},
    "iofed":            {"vocab": ["raid", "first_fvg", "validated", "invalidated"],
                         "map": {"raid": "waiting", "first_fvg": "waiting",
                                 "validated": "valid", "invalidated": "rejected"}},
}


def common_state(model: str, lifecycle: str) -> str:
    """Map a model's lifecycle sub-state to the ONE common state the engine uses."""
    return LIFECYCLE.get(model, {}).get("map", {}).get(lifecycle, "waiting")


@dataclass
class Entry:
    """THE COMMON CONTRACT every execution model implements. The engine + dashboard consume this
    generic object without knowing the model. `state` is the common state (all the engine reads);
    `lifecycle` is the model-specific sub-state (dashboard only), derived → common if `state` omitted."""
    model: str                    # registry key: "fvg" | "order_block" | "breaker" | ...
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


# The course execution models, as first-class registry entries. `implemented` flags whether a real,
# verified detector exists yet; `detect` is the detector (None until implemented). Descriptions are
# kept faithful to how the course frames each model.
REGISTRY: dict[str, dict] = {
    "fvg": {
        "implemented": True, "detect": None,   # FVG entries come from v1's frozen detector (see pipeline)
        "desc": "Fair Value Gap — 3-candle imbalance; entry at CE (50%). The B8 default entry.",
    },
    "order_block": {
        "implemented": True, "detect": None,   # detector assigned below (order_block_entries)
        "desc": "Order Block — last opposing-close candle before the displacement; entry at its 50%.",
    },
    "breaker": {
        "implemented": True, "detect": None,   # detector assigned below (breaker_entries)
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


# --- Order Block detector: the first from-scratch, candle-body model (needs raw bars, v1.1) --------
_OB_LOOKBACK = 10          # scan at most this many bars back from the impulse origin for the OB candle


def order_block_entries(disp, mss, ms, direction, bars) -> list:
    """The Order Block entry on one displacement leg, as a generic Entry.

    OB (course B8) = the LAST opposing-close candle immediately before the impulse: a down-close
    candle before a bullish displacement (→ long), an up-close candle before a bearish one (→ short).
    Entry ref = the OB's 50% (mean threshold (high+low)/2); invalidation = the OB low (long) / high
    (short). Causal / no-repaint: the OB is fixed at its formation bar, and its lifecycle is read only
    from realised bars up to the cursor:
      identified → (impulse exhausts) awaiting_retest → (price returns into the zone) validated;
      a close beyond the invalidation before the retest ⇒ invalidated.
    Needs the raw `bars` (v1.1) — v1 never pre-computes order blocks onto `ms`."""
    if not bars:
        return []
    j = getattr(disp, "start_index", -1)                      # manipulation-extreme bar = impulse origin
    if j < 0 or j >= len(bars):
        return []
    bullish = disp.direction == "bullish"
    ob_i = None                                               # last opposing-body candle at/ before origin
    for i in range(j, max(-1, j - _OB_LOOKBACK) - 1, -1):
        b = bars[i]
        if bullish and b.close < b.open:                     # down candle before an up impulse
            ob_i = i; break
        if not bullish and b.close > b.open:                 # up candle before a down impulse
            ob_i = i; break
    if ob_i is None:
        return []
    ob = bars[ob_i]
    d = "long" if bullish else "short"
    ref = (ob.high + ob.low) / 2.0                            # mean threshold (50%)
    inval = ob.low if bullish else ob.high

    inval_i = retest_i = None                                 # earliest decisive events after formation
    for i in range(ob_i + 1, len(bars)):
        b = bars[i]
        if inval_i is None and ((bullish and b.close < ob.low) or (not bullish and b.close > ob.high)):
            inval_i = i                                       # a close beyond the OB kills it
    if getattr(disp, "exhausted", False):                    # only look for a retest once the impulse ends
        for i in range(disp.end_index + 1, len(bars)):
            b = bars[i]
            if (bullish and b.low <= ob.high) or (not bullish and b.high >= ob.low):
                retest_i = i; break                           # price traded back into the OB zone

    if retest_i is not None and (inval_i is None or retest_i <= inval_i):
        lifecycle = "validated"
    elif inval_i is not None:
        lifecycle = "invalidated"
    elif getattr(disp, "exhausted", False):
        lifecycle = "awaiting_retest"
    else:
        lifecycle = "identified"

    return [Entry(model="order_block", direction=d, ref=ref, invalidation=inval,
                  lifecycle=lifecycle, id=f"ob-{ob_i}-{disp.direction}", origin_index=ob_i, source=ob)]


REGISTRY["order_block"]["detect"] = order_block_entries


# --- Breaker Block detector: a FAILED order block whose polarity flips after a structure break -----
_BRK_LOOKBACK = 30         # scan at most this many bars back from the impulse origin for the failed OB


def breaker_entries(disp, mss, ms, direction, bars) -> list:
    """The Breaker Block entry on one displacement leg, as a generic Entry.

    A breaker is an ORDER BLOCK THAT FAILED and then flips polarity once structure breaks the other
    way (course B8). What makes it a breaker — not just an OB — is (1) a confirmed market-structure
    shift on this leg and (2) the block was VIOLATED before the break: for a long, a prior down-close
    candle whose HIGH was rallied through (an up-attempt) and whose LOW was then taken out (the move
    failed / liquidity swept); the current up-displacement + MSS reclaims it, so that candle now acts
    as support. Entry ref = the breaker candle's 50%; invalidation = its far edge (low for a long,
    high for a short). Mirror for a short.

    Requires the raw `bars` (v1.1) and a confirmed `mss` (no structure break ⇒ no breaker, at most an
    OB). Causal / no-repaint: the breaker candle is fixed at its bar; the up-attempt→failure sequence
    and the retest are read only from realised bars up to the cursor. Lifecycle:
    formed → (price retests the zone after the break) confirmed; a close beyond invalidation ⇒ invalidated."""
    if not bars or mss is None or getattr(mss, "state", "") != "confirmed":
        return []                                           # a breaker needs a real structure break
    j = getattr(disp, "start_index", -1)                    # impulse origin (= manipulation extreme)
    if j <= 0 or j >= len(bars):
        return []
    bullish = disp.direction == "bullish"
    brk_i = None
    for i0 in range(j - 1, max(-1, j - _BRK_LOOKBACK) - 1, -1):
        B = bars[i0]
        opposing = (B.close < B.open) if bullish else (B.close > B.open)   # the failed-move origin
        if not opposing:
            continue
        if bullish:                                         # up-attempt (rally above B.high)…
            rally = next((a for a in range(i0 + 1, j + 1) if bars[a].high > B.high), None)
            failed = rally is not None and any(bars[b].low < B.low for b in range(rally + 1, j + 1))
        else:                                               # down-attempt (drop below B.low)…
            drop = next((a for a in range(i0 + 1, j + 1) if bars[a].low < B.low), None)
            failed = drop is not None and any(bars[b].high > B.high for b in range(drop + 1, j + 1))
        if failed:                                          # …then the level was taken out (failure)
            brk_i = i0; break
    if brk_i is None:
        return []
    B = bars[brk_i]
    d = "long" if bullish else "short"
    ref = (B.high + B.low) / 2.0
    inval = B.low if bullish else B.high

    # The block's LOW being taken out is what FORMS the breaker (the failure), not what invalidates it.
    # So the breaker's own invalidation — and its retest — are read only AFTER the impulse reclaims it.
    inval_i = retest_i = None
    if getattr(disp, "exhausted", False):
        for i in range(disp.end_index + 1, len(bars)):
            b = bars[i]
            if inval_i is None and ((bullish and b.close < B.low) or (not bullish and b.close > B.high)):
                inval_i = i
            if retest_i is None and ((bullish and b.low <= B.high) or (not bullish and b.high >= B.low)):
                retest_i = i

    if retest_i is not None and (inval_i is None or retest_i <= inval_i):
        lifecycle = "confirmed"
    elif inval_i is not None:
        lifecycle = "invalidated"
    else:
        lifecycle = "formed"

    return [Entry(model="breaker", direction=d, ref=ref, invalidation=inval,
                  lifecycle=lifecycle, id=f"brk-{brk_i}-{disp.direction}", origin_index=brk_i, source=B)]


REGISTRY["breaker"]["detect"] = breaker_entries


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
