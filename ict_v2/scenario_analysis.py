"""Scenario-analysis layer — READ-ONLY. Nothing here mutates the restored trading engine, its
trade generation, filters, thresholds, or frequency. It only READS the engine's existing outputs
(`engine.strategic` / `engine.intraday` for nested ranges + P/D, and `v1.analyze(bars, tf)` for the
FVG / displacement / MSS causal chain) and derives an auditable scenario picture on top.

What it produces (all deterministic, no new numeric threshold):
  • nested ranges — parent (context TF, e.g. 4H) + internal (setup TF, e.g. 1H), each with 0/0.5/1
    and the current price's Premium/Discount classification;
  • an FVG catalog with the approved lifecycle state machine
      FRESH → TOUCHED → RESPECTED / FAILED / CLOSED, where RESPECTED and FAILED are bound to the
      reaction that actually departs from THAT FVG (via the engine's own FVG→displacement→MSS
      `depends_on` chain), and where that binding is absent the state stays TOUCHED /
      confirmation-unresolved (never guessed);
  • an absence taxonomy — every raw A1 geometric gap the engine did NOT emit as a first-class FVG,
    with the reason (chiefly: not inside an MSS-displacement leg, A5/B5), so a disagreement with a
    hand-drawn chart is auditable rather than silently resolved;
  • liquidity draws (nearest / next) from the engine's objectives;
  • two mutually-exclusive conditional scenarios (bullish / bearish) + a WAIT state, each carrying an
    analysis-only lifecycle label (never an order state).

`test_count` is intentionally left null in v1 (no visit-count definition yet).
"""
from __future__ import annotations

from ict_live.engine import pipeline as v1        # frozen v1 engine, read-only — same import the engine uses
from ict_v2 import liquidity_runs as LR            # standalone HRLR/LRLR model (read-only)

FIB = (0.0, 0.5, 1.0)                              # the levels drawn on the chart (0 / 0.5·EQ / 1)


# ─────────────────────────────────────────────────────────────────────────────
# FVG lifecycle state machine (approved definitions)
# ─────────────────────────────────────────────────────────────────────────────

def _opposite(direction: str) -> str:
    return "bearish" if direction == "bullish" else "bullish"


def _mitigation_index(f, bars) -> int | None:
    """Recompute the acceptance-through bar with the engine's EXACT A3 rule (note 1, approved): the
    first bar after formation whose BODY closes through the FAR boundary. Returns None if unmitigated."""
    for k in range(f.formed_index + 1, len(bars)):
        b = bars[k]
        if f.direction == "bullish" and b.close < f.bottom:
            return k
        if f.direction == "bearish" and b.close > f.top:
            return k
    return None


def fvg_state(f, displacements, mss_list, bars) -> tuple[str, dict]:
    """Return (STATE, audit) for one FVG. Binding is within the FVG's own timeframe (exact indices).

    STATE ∈ {FRESH, TOUCHED, RESPECTED, FAILED, CLOSED}. TOUCHED carries
    `confirmation="unresolved"` in the audit when it reached CE but no causally-bound reaction MSS
    confirms it — per the refinement, this is NOT upgraded to RESPECTED on any unrelated later MSS.
    """
    disp_by_id = {d.id: d for d in displacements}
    reached_ce = f.first_touch_index is not None
    mit = _mitigation_index(f, bars)
    audit: dict = {"reached_ce": reached_ce, "first_touch_index": f.first_touch_index,
                   "mitigation_index": mit, "bound_mss": None, "bound_displacement": None,
                   "confirmation": None}

    # a confirmed MSS whose displacement leg ORIGINATES INSIDE this FVG and starts AFTER the touch
    # → the reaction that departs from the FVG in the FVG's own direction (RESPECTED binding)
    def _reaction_departing():
        if f.first_touch_index is None:
            return None
        for m in mss_list:
            if m.state != "confirmed" or m.direction != f.direction or not m.depends_on:
                continue
            d = disp_by_id.get(m.depends_on[0])
            if d is None:
                continue
            if (f.bottom <= d.start_price <= f.top) and d.start_index >= f.first_touch_index:
                return (m, d)
        return None

    # a confirmed OPPOSITE MSS whose displacement SPAN contains the acceptance-through bar
    # → the move that accepts through / departs opposite (FAILED binding)
    def _acceptance_departing():
        if mit is None:
            return None
        opp = _opposite(f.direction)
        for m in mss_list:
            if m.state != "confirmed" or m.direction != opp or not m.depends_on:
                continue
            d = disp_by_id.get(m.depends_on[0])
            if d is None:
                continue
            if d.start_index <= mit <= d.end_index:
                return (m, d)
        return None

    # resolution order: acceptance-through dominates (terminal), then reaction, then touch/fresh
    if mit is not None:
        bind = _acceptance_departing()
        if bind is not None:
            m, d = bind
            audit.update(bound_mss=m.id, bound_displacement=d.id, confirmation="opposite-confirmed")
            return "FAILED", audit
        audit["confirmation"] = "mitigated-no-opposite-confirmation"
        return "CLOSED", audit

    if reached_ce:
        bind = _reaction_departing()
        if bind is not None:
            m, d = bind
            audit.update(bound_mss=m.id, bound_displacement=d.id, confirmation="same-confirmed")
            return "RESPECTED", audit
        audit["confirmation"] = "unresolved"          # touched, but no reaction departing the FVG yet
        return "TOUCHED", audit

    return "FRESH", audit


# ─────────────────────────────────────────────────────────────────────────────
# Absence taxonomy — raw A1 gaps the engine did NOT emit as first-class FVGs
# ─────────────────────────────────────────────────────────────────────────────

def _raw_gaps(bars) -> list[dict]:
    """Every 3-candle A1 imbalance on this bar list (geometry only, no MSS eligibility filter)."""
    out = []
    for i in range(1, len(bars) - 1):
        a, c = bars[i - 1], bars[i + 1]
        if a.high < c.low:
            out.append({"mid_index": i, "direction": "bullish", "top": c.low, "bottom": a.high})
        elif a.low > c.high:
            out.append({"mid_index": i, "direction": "bearish", "top": a.low, "bottom": c.high})
    return out


def absent_gaps(bars, emitted_fvgs) -> list[dict]:
    """Raw geometric gaps present on the chart but NOT emitted as first-class FVGs by the engine,
    each with an explicit reason so a hand-chart disagreement is auditable (never silently resolved)."""
    emitted = {(f.mid_index, f.direction) for f in emitted_fvgs}
    out = []
    for g in _raw_gaps(bars):
        if (g["mid_index"], g["direction"]) in emitted:
            continue
        g = dict(g)
        g["reason"] = "not inside an MSS-displacement leg (A5/B5 eligibility)"
        g["ce"] = round((g["top"] + g["bottom"]) / 2.0, 2)
        out.append(g)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Assembling the read-only picture
# ─────────────────────────────────────────────────────────────────────────────

def _range_view(ctx, price, role, tf) -> dict | None:
    """Nested-range view (parent/internal) with 0/0.5/1 and the price's Premium/Discount."""
    dr = getattr(ctx, "dealing_range", None) if ctx else None
    if dr is None:
        return {"role": role, "tf": tf, "available": False,
                "reason": "engine has no dealing range on this timeframe"}
    fibs = {f["level"]: f["price"] for f in ctx.fib_levels()}
    return {"role": role, "tf": getattr(dr, "source_tf", tf), "available": True,
            "low": round(dr.low, 2), "high": round(dr.high, 2), "eq": round(dr.ce, 2),
            "direction": dr.direction,
            "fib": {"0": fibs.get(0.0), "0.5": fibs.get(0.5), "1": fibs.get(1.0)},
            "price_class": dr.zone_of(price).upper() if price is not None else None}


def _fvg_catalog(bars, tf) -> tuple[list[dict], list[dict]]:
    """Emitted FVGs (with lifecycle state) + the absence taxonomy, for one timeframe."""
    if not bars:
        return [], []
    ms = v1.analyze(bars, tf)                          # read-only structural read (same call the engine uses)
    fvgs = [r.item for r in ms.ranked_fvgs]
    disps = [r.item for r in ms.ranked_displacements]
    mss = [r.item for r in ms.ranked_mss]
    cat = []
    for f in fvgs:
        state, audit = fvg_state(f, disps, mss, bars)
        cat.append({
            "tf": tf, "direction": f.direction, "top": round(f.top, 2), "bottom": round(f.bottom, 2),
            "ce": round(f.ce, 2), "state": state, "engine_status": f.status, "test_count": None,
            "relevant": state not in ("FAILED", "CLOSED"), "audit": audit,
        })
    return cat, absent_gaps(bars, fvgs)


def _liquidity_draws(objectives, price) -> dict:
    """Nearest / next BSL above and SSL below from the engine's existing objective set."""
    swings = [o for o in (objectives or []) if o.get("kind") == "swing" and o.get("price") is not None]
    above = sorted([o for o in swings if o["price"] > (price or 0) and o.get("label") == "BSL"],
                   key=lambda o: o["price"])
    below = sorted([o for o in swings if o["price"] < (price or 1e18) and o.get("label") == "SSL"],
                   key=lambda o: -o["price"])

    def _v(o):
        return {"label": o.get("label"), "price": round(o["price"], 2), "tf": o.get("tf"),
                "class": o.get("liquidity_class"), "swept": o.get("status") == "swept"}
    return {"nearest_bsl": _v(above[0]) if above else None,
            "next_bsl": _v(above[1]) if len(above) > 1 else None,
            "nearest_ssl": _v(below[0]) if below else None,
            "next_ssl": _v(below[1]) if len(below) > 1 else None}


def _scenarios(parent, internal, catalog, draws, price, engine_scenarios) -> list[dict]:
    """Two mutually-exclusive conditional paths + WAIT, with analysis-only lifecycle labels.

    Bullish path keys off a bullish FVG in the INTERNAL discount half; bearish path keys off the
    parent PREMIUM / that FVG failing. Direction/draw come from structure + liquidity; exact
    execution geometry is only borrowed from the engine when it already produced a matching scenario
    (never invented here). Lifecycle labels are analysis state, NOT order state.
    """
    internal_disc = internal and internal.get("price_class") == "DISCOUNT"
    parent_prem = parent and parent.get("price_class") == "PREMIUM"
    relevant_bull = [f for f in catalog if f["direction"] == "bullish" and f["relevant"]]
    failed_bull = [f for f in catalog if f["direction"] == "bullish" and f["state"] == "FAILED"]
    # the nearest still-relevant bullish FVG at/below price = the long WHERE candidate
    below = sorted([f for f in relevant_bull if f["top"] <= (price or 1e18)], key=lambda f: -(f["top"]))
    long_fvg = below[0] if below else (relevant_bull[0] if relevant_bull else None)

    _LIFE = {
        "FRESH":     ("AWAITING FVG RETEST", "price has not returned to the gap"),
        "TOUCHED":   ("AWAITING BULLISH CONFIRMATION", "reached CE; no departing reaction MSS yet"),
        "RESPECTED": ("BULLISH PATH ACTIVE", "reaction departed the FVG and broke structure up"),
    }
    if long_fvg is not None:
        life, why = _LIFE[long_fvg["state"]]
    elif failed_bull:
        life, why = "INVALIDATED", "the bullish FVG in the internal discount failed (accepted through + bearish MSS)"
    else:
        life, why = "WAIT / UNRESOLVED", "no relevant bullish FVG in the internal discount"
    eng_by_dir = {s.get("direction"): s for s in (engine_scenarios or [])}

    def _geo(direction):
        s = eng_by_dir.get(direction)
        ex = (s or {}).get("execution") or {}
        if ex.get("entry") is None:
            return None
        return {"entry": ex.get("entry"), "stop": ex.get("stop"), "target": ex.get("target"),
                "rr": ex.get("rr"), "state": s.get("state"),
                "note": "borrowed from engine's live scenario (≥2R enforced there)"}

    bull = {
        "id": "A", "path": "bullish", "state": life,
        "why": why,
        "where": (f"internal DISCOUNT + bullish FVG {long_fvg['bottom']}–{long_fvg['top']}"
                  if long_fvg else "no bullish FVG in internal discount"),
        "confirmation": "FVG respected (reaction departs the zone) + bullish MSS",
        "direction": "LONG",
        "draw": draws.get("nearest_bsl"),
        "next": draws.get("next_bsl"),
        "fvg": long_fvg, "geometry": _geo("long"),
        "context_ok": bool(internal_disc),
    }
    bear = {
        "id": "B", "path": "bearish",
        "state": "BEARISH PATH ACTIVE" if failed_bull else "AWAITING BEARISH CONFIRMATION",
        "why": "parent PREMIUM / bullish-FVG failure → short toward downside liquidity",
        "where": "parent PREMIUM (context) / acceptance through the bullish FVG",
        "confirmation": "acceptance through the FVG + bearish MSS (invalidates A)",
        "direction": "SHORT",
        "draw": draws.get("nearest_ssl"),
        "next": draws.get("next_ssl"),
        "geometry": _geo("short"),
        "context_ok": bool(parent_prem),
    }
    wait = {
        "id": "C", "path": "unresolved", "state": "WAIT / UNRESOLVED",
        "why": "price inside the decision band with no departing confirmation either way",
        "active": not (bull["state"] in ("BULLISH PATH ACTIVE",) or bear["state"] == "BEARISH PATH ACTIVE"),
    }
    return [bull, bear, wait]


def analyze(live) -> dict:
    """The whole read-only picture for one V2Live. Never mutates `live` or its engine."""
    eng = live.engine
    parent_ctx, internal_ctx = eng.strategic, eng.intraday
    # last traded price — trigger bar close, then confirm, then setup
    price = None
    for buf in (eng._trigger_bars, eng._confirm_bars, live.buf.get(live.setup_tf)):
        if buf:
            price = float(buf[-1].close)
            break

    parent = _range_view(parent_ctx, price, "parent", live.context_tf)
    internal = _range_view(internal_ctx, price, "internal", live.setup_tf)

    # FVG catalog + absence taxonomy on the context and setup timeframes
    catalog, absent = [], []
    for bars, tf in ((eng._ctx_bars, live.context_tf), (live.buf.get(live.setup_tf), live.setup_tf)):
        c, a = _fvg_catalog(bars, tf)
        catalog += c
        absent += a

    snap = live.snapshot()
    draws = _liquidity_draws(snap.get("objectives"), price)
    scenarios = _scenarios(parent, internal, catalog, draws, price, snap.get("scenarios"))

    # standalone HRLR/LRLR liquidity-run model, computed on the SAME confirm-TF candles the chart
    # draws (so pivot_index aligns with the candle series). Read-only; never feeds execution.
    lr_bars = (snap.get("bars") or {}).get(live.confirm_tf) or []
    liquidity_runs = LR.summary(LR.detect_liquidity_runs(lr_bars, tick=0.25))
    liquidity_runs["tf"] = live.confirm_tf
    liquidity_runs["tick"] = 0.25

    return {
        "price": price,
        "ranges": {"parent": parent, "internal": internal},
        "fvgs": catalog,
        "absent_fvgs": absent,
        "liquidity": draws,
        "liquidity_runs": liquidity_runs,
        "scenarios": scenarios,
        "notes": {
            "read_only": True,
            "test_count": "null in v1 (no visit-count definition)",
            "binding": "RESPECTED/FAILED bound to the reaction departing the FVG via depends_on; "
                       "unresolved stays TOUCHED (never guessed)",
            "parent_range": "context TF (4H) used as parent; Daily/Weekly not plumbed in v1 (G1)",
            "eqh_eql": "unavailable in v1 — no course tolerance (G4)",
        },
    }
