"""MNQ analyst — READ-ONLY decision-support, split into logical sub-models.

Mechanizes the user's market-search algorithm on top of the standalone indicators
(market_structure, order_blocks, liquidity_runs) across timeframes. It is a TOOL that structures a
discretionary process and computes conditional levels + position sizing under the account rules — it
is NOT investment advice, NOT a directional prediction, and NOT a profitability claim. Nothing here
touches the trading engine or generates orders.

Sub-models (each a pure function over per-TF bar lists [{o,h,l,c,t}, ...]):
  context()         — 30m/15m bias, impulse/correction, external/internal liquidity, EQH/EQL, HTF OB
  location()        — nearest actionable 5m location (OB / FVG-less here / HRLR / pool / range edge)
  liquidity_event() — recent sweep + reclaim; rejection / acceptance / displacement (heuristic)
  structure()       — the relevant swing + BOS/MSS at 5m and 1m
  trigger()         — 1m break -> retest -> second-break stage status (reports stages, predicts none)
  risk_size()       — MNQ sizing table + $ risk + R to targets under the account ceiling
  grade()           — A+/A/B/none from location quality + trigger completion + room
  report()          — assembles verdict (🟢/🟡/🔴) + concise Hebrew lines

Heuristic classifications (impulse/correction, rejection/acceptance/displacement, confluence) are
marked `heuristic` in the output — they are cues to verify on the chart, not ground truth.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ict_v2 import market_structure as MS
from ict_v2 import order_blocks as OB
from ict_v2 import liquidity_runs as LR

# account rules (per account)
PER_TRADE_RISK = 150.0          # $ ceiling per account
POINT_VALUE = 2.0               # MNQ $/pt/contract
MAX2_STOP_PTS = 37.5            # <= -> up to 2 contracts
MAX1_STOP_PTS = 75.0           # <= -> 1 contract; beyond -> no trade
MIN_R_A = 2.0                   # A needs >= 2R room
MIN_R_APLUS = 2.5              # A+ prefers >= 2.5R


def _c(b): return float(b["c"])
def _h(b): return float(b["h"])
def _l(b): return float(b["l"])


# ── sub-model: higher-timeframe context (30m + 15m) ──────────────────────────
def context(b30, b15, price):
    ev30 = MS.detect_market_structure(b30, pivot_strength=3)
    ev15 = MS.detect_market_structure(b15, pivot_strength=3)
    last30 = ev30[-1] if ev30 else None
    bias = "neutral"
    impulse = None
    if last30:
        bias = "long" if last30.direction == "bull" else "short"
        impulse = "impulse" if last30.kind == "BOS" else "shift(correction→new)"
    # external liquidity from 30m/15m unmitigated swing pools (HRLR sweeps + LRLR EQ levels)
    runs = LR.detect_liquidity_runs(b30, tick=0.25) + LR.detect_liquidity_runs(b15, tick=0.25)
    unmit = [r for r in runs if not r.mitigated]
    bsl = sorted([r.price for r in unmit if r.is_high and r.price > price])
    ssl = sorted([r.price for r in unmit if (not r.is_high) and r.price < price], reverse=True)
    eqh = [round(r.price, 2) for r in unmit if r.is_high and r.kind == "LRLR"]
    eql = [round(r.price, 2) for r in unmit if (not r.is_high) and r.kind == "LRLR"]
    # HTF order blocks (30m) that could obstruct a proposed trade
    obs30 = [o for o in OB.detect_order_blocks(b30, swing_len=3, max_bars=100) if o.state == "active"]
    htf_ob = [{"dir": "bull" if o.is_bull else "bear", "top": round(o.top, 2), "bottom": round(o.bottom, 2)}
              for o in obs30]
    return {"bias30": bias, "impulse": impulse, "heuristic": True,
            "external_bsl": [round(x, 2) for x in bsl[:3]], "external_ssl": [round(x, 2) for x in ssl[:3]],
            "eqh": eqh[:3], "eql": eql[:3], "htf_ob_30m": htf_ob[:4],
            "events30": MS.summary(ev30)["total"], "obstacle15": _obstacle15(ev15, price)}


def _obstacle15(ev15, price):
    # nearest opposing 15m structure level that could cap reward
    if not ev15:
        return None
    e = ev15[-1]
    return {"kind": e.kind, "dir": e.direction, "level": round(e.level, 2)}


# ── sub-model: actionable 5m location ────────────────────────────────────────
def location(b5, price):
    obs = [o for o in OB.detect_order_blocks(b5, swing_len=3, max_bars=100) if o.state == "active"]
    runs = [r for r in LR.detect_liquidity_runs(b5, tick=0.25) if not r.mitigated]
    cands = []
    for o in obs:
        mid = (o.top + o.bottom) / 2
        cands.append({"type": "OB", "dir": "demand" if o.is_bull else "supply",
                      "top": round(o.top, 2), "bottom": round(o.bottom, 2), "ref": round(mid, 2),
                      "dist": abs(mid - price)})
    for r in runs:
        cands.append({"type": r.kind, "dir": "supply" if r.is_high else "demand",
                      "top": round(r.price, 2), "bottom": round(r.price, 2), "ref": round(r.price, 2),
                      "dist": abs(r.price - price)})
    cands.sort(key=lambda c: c["dist"])
    nearest = cands[0] if cands else None
    # confluence = how many other locations sit within ~3 pts of the nearest
    conf = 0
    if nearest:
        conf = sum(1 for c in cands if abs(c["ref"] - nearest["ref"]) <= 3.0) - 1
    return {"nearest": nearest, "confluence": conf, "count": len(cands), "heuristic": True}


# ── sub-model: liquidity event (sweep + reclaim) ─────────────────────────────
def liquidity_event(b5, price):
    runs = LR.detect_liquidity_runs(b5, tick=0.25)
    sweeps = [r for r in runs if r.kind == "HRLR"]
    last = sweeps[-1] if sweeps else None
    if not last:
        return {"sweep": None, "reclaim": None, "heuristic": True}
    return {"sweep": {"side": last.side, "price": round(last.price, 2), "mitigated": last.mitigated},
            "reclaim": "check on chart (rejection vs acceptance)", "heuristic": True}


# ── sub-model: structure (5m + 1m) ───────────────────────────────────────────
def structure(b5, b1):
    e5 = MS.detect_market_structure(b5, pivot_strength=3)
    e1 = MS.detect_market_structure(b1, pivot_strength=3)
    def last(ev):
        e = ev[-1] if ev else None
        return None if e is None else {"kind": e.kind, "dir": e.direction, "level": round(e.level, 2)}
    return {"m5": last(e5), "m1": last(e1)}


# ── sub-model: 1m trigger sequence (reports stages, predicts none) ───────────
def trigger(b1):
    e1 = MS.detect_market_structure(b1, pivot_strength=2)
    n = len(e1)
    # break -> retest -> second break, described only from completed events
    stages = {"initial_break": n >= 1, "second_break": n >= 2}
    last = e1[-1] if e1 else None
    return {"stages": stages, "events": n,
            "last": None if not last else {"kind": last.kind, "dir": last.direction, "level": round(last.level, 2)},
            "note": "retest is discretionary — verify on the 1m chart; not auto-confirmed"}


# ── invalidation / stop placement ────────────────────────────────────────────
def stop_level(direction, nd, sweep, buffer=2.0):
    """Stop BEYOND the actual setup structure: the farther of {order-block boundary, swept liquidity}
    on the invalidation side, plus a buffer. A sweep-based stop therefore always sits past the raided
    high (short) / low (long) — never inside it."""
    if nd is None:
        return None
    if direction == "SHORT":
        inval = nd["top"]
        if sweep and sweep.get("side") == "BSL" and sweep.get("price", inval) > inval:
            inval = sweep["price"]
        return round(inval + buffer, 2)
    if direction == "LONG":
        inval = nd["bottom"]
        if sweep and sweep.get("side") == "SSL" and sweep.get("price", inval) < inval:
            inval = sweep["price"]
        return round(inval - buffer, 2)
    return None


# ── sub-model: risk / sizing / targets under the account rules ───────────────
def risk_size(entry, stop, targets):
    if entry is None or stop is None:
        return {"available": False, "reason": "entry/stop not defined — conditional"}
    stop_pts = round(abs(entry - stop), 2)
    if stop_pts > MAX1_STOP_PTS:
        return {"available": False, "stop_pts": stop_pts, "contracts": 0,
                "reason": f"stop {stop_pts}pt > {MAX1_STOP_PTS} — no trade"}
    contracts = 2 if stop_pts <= MAX2_STOP_PTS else 1
    risk = round(stop_pts * POINT_VALUE * contracts, 2)
    while risk > PER_TRADE_RISK and contracts > 1:
        contracts -= 1
        risk = round(stop_pts * POINT_VALUE * contracts, 2)
    rr = []
    for t in (targets or []):
        if t is not None and stop_pts > 0:
            rr.append(round(abs(t - entry) / stop_pts, 2))
    return {"available": risk <= PER_TRADE_RISK, "stop_pts": stop_pts, "contracts": contracts,
            "risk_per_account": risk, "R_to_targets": rr,
            "note": "per account; combined exposure via copier is double"}


# ── sub-model: grade ─────────────────────────────────────────────────────────
def grade(loc, ev, struct, trig, best_R):
    if not loc.get("nearest"):
        return {"grade": None, "why": "no preplanned 5m location"}
    if best_R is not None and best_R < 1.5:
        return {"grade": None, "why": f"insufficient room ({best_R}R)"}
    trigger_done = bool(trig["stages"]["second_break"])
    swept = ev.get("sweep") is not None
    if swept and trigger_done and best_R and best_R >= MIN_R_APLUS and loc.get("confluence", 0) >= 1:
        g = "A+"
    elif trigger_done and best_R and best_R >= MIN_R_A:
        g = "A"
    elif best_R and best_R >= 1.5:
        g = "B"
    else:
        g = None
    return {"grade": g, "trigger_completed": trigger_done, "why": "location+room+trigger composite"}


# ── report assembly ──────────────────────────────────────────────────────────
def analyze(series_by_tf, symbol, *, price=None):
    b30 = series_by_tf.get("30m") or []
    b15 = series_by_tf.get("15m") or []
    b5 = series_by_tf.get("5m") or []
    b1 = series_by_tf.get("1m") or []
    if price is None:
        for buf in (b1, b5, b15, b30):
            if buf:
                price = _c(buf[-1])
                break
    if price is None or not b5:
        return {"verdict": "🔴 No Trade", "symbol": symbol,
                "lines": ["🔴 No Trade", "Insufficient 5m/1m data — cannot analyze right now."],
                "conditional": True}

    ctx = context(b30, b15, price)
    loc = location(b5, price)
    ev = liquidity_event(b5, price)
    st = structure(b5, b1)
    tr = trigger(b1)

    # a CONSERVATIVE conditional entry proposal from the nearest 5m location (never a live fill).
    # Invalidation is placed BEYOND the actual setup structure — the FARTHER of {order-block boundary,
    # the swept liquidity level} — so a sweep-based stop always sits past the raided high/low, plus a
    # buffer. (Bug fix: previously the stop used only the OB boundary and could land inside the sweep.)
    entry = stop = tp1 = tp2 = None
    direction = None
    sw = (ev or {}).get("sweep")
    if loc["nearest"]:
        nd = loc["nearest"]
        if nd["dir"] == "demand" and ctx["bias30"] in ("long", "neutral"):
            direction = "LONG"
            entry = nd["ref"]; stop = stop_level("LONG", nd, sw)
            tp1 = ctx["external_bsl"][0] if ctx["external_bsl"] else None
            tp2 = ctx["external_bsl"][1] if len(ctx["external_bsl"]) > 1 else None
        elif nd["dir"] == "supply" and ctx["bias30"] in ("short", "neutral"):
            direction = "SHORT"
            entry = nd["ref"]; stop = stop_level("SHORT", nd, sw)
            tp1 = ctx["external_ssl"][0] if ctx["external_ssl"] else None
            tp2 = ctx["external_ssl"][1] if len(ctx["external_ssl"]) > 1 else None

    rs = risk_size(entry, stop, [tp1, tp2])
    best_R = max(rs.get("R_to_targets") or [0]) if rs.get("R_to_targets") else None
    gr = grade(loc, ev, st, tr, best_R)

    # data freshness — never issue a live verdict on stale bars
    last_t = (b1[-1].get("t") if b1 else None) or (b5[-1].get("t") if b5 else None)
    stale, age_min = True, None
    if last_t:
        try:
            t = datetime.fromisoformat(last_t)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - t).total_seconds()
            age_min = round(age / 60, 1)
            stale = age > 300                    # > 5 min = not a live feed
        except Exception:
            pass

    # direction conflict — proposed side vs the most recent 1m structure
    m1 = st.get("m1")
    conflict = bool(direction and m1 and (
        (direction == "LONG" and m1["dir"] == "bear") or (direction == "SHORT" and m1["dir"] == "bull")))

    flags = []
    verdict = "🔴 No Trade"
    if stale:
        flags.append(f"Data not live (last update {age_min} min ago) — no entry; run run-live.sh during market hours")
    elif loc["nearest"] and direction and rs.get("available"):
        if conflict:
            verdict = "🟡 Hold"
            flags.append("Direction conflict: 1m structure opposes the proposed side — don't fade structure without confirmation")
        elif gr["grade"] in ("A", "A+") and tr["stages"]["second_break"] and (best_R or 0) >= MIN_R_A:
            verdict = "🟢 Entry"
            flags.append("Sequence appears complete — this is a prompt to verify the retest + brackets yourself, not an order")
        elif gr["grade"] in ("A", "A+", "B"):
            verdict = "🟡 Hold"
    elif loc["nearest"] and direction:
        verdict = "🟡 Hold"

    lines = _report_lines(verdict, symbol, price, direction, gr, ctx, loc, ev, st, tr, entry, stop, rs,
                          tp1, tp2, best_R, flags)
    return {"verdict": verdict, "symbol": symbol, "price": round(price, 2), "direction": direction,
            "grade": gr.get("grade"), "context": ctx, "location": loc, "liquidity_event": ev,
            "structure": st, "trigger": tr, "risk": rs,
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "best_R": best_R,
            "flags": flags, "stale": stale, "age_min": age_min, "conflict": conflict,
            "conditional": verdict != "🟢 Entry", "lines": lines,
            "disclaimer": "Decision-support tool only — not advice, not a forecast, not a profit guarantee."}


def _f(x):
    return "—" if x is None else f"{x:,.2f}"


def _report_lines(verdict, sym, price, direction, gr, ctx, loc, ev, st, tr, entry, stop, rs, tp1, tp2, best_R, flags=None):
    g = gr.get("grade") or "—"
    nd = loc.get("nearest")
    locdesc = "—" if not nd else f"{nd['type']} {nd['dir']} @ {_f(nd['ref'])} (confluence {loc['confluence']})"
    sweep = ev.get("sweep")
    swdesc = "no recent sweep" if not sweep else f"sweep {sweep['side']} @ {_f(sweep['price'])} ({'mitigated' if sweep['mitigated'] else 'open'})"
    m5 = st.get("m5"); m1 = st.get("m1")
    trg = "not complete" if not tr["stages"]["second_break"] else "two-break sequence present (verify retest on chart)"
    lines = [
        verdict,
        f"{sym} @ {_f(price)} · dir {direction or '—'} · grade {g} · (heuristic; verify on chart)",
        f"30m context: bias {ctx['bias30']} · {ctx.get('impulse') or '—'} · 15m obstacle: {ctx.get('obstacle15') or '—'}",
        f"External liquidity: BSL {ctx['external_bsl'] or '—'} · SSL {ctx['external_ssl'] or '—'} · EQH {ctx['eqh'] or '—'} EQL {ctx['eql'] or '—'}",
        f"5m location: {locdesc} · liquidity event: {swdesc}",
        f"Structure 5m: {m5 or '—'} · 1m: {m1 or '—'} · 1m trigger: {trg}",
    ]
    for fl in reversed(flags or []):
        lines.insert(2, "⚠ " + fl)
    if entry is not None:
        contracts = rs.get("contracts", 0)
        lines.append(f"Conditional: entry ~{_f(entry)} · invalidation/stop ~{_f(stop)} · {rs.get('stop_pts','—')}pt · "
                     f"{contracts} MNQ · risk ${rs.get('risk_per_account','—')}/account")
        lines.append(f"TP1 {_f(tp1)} · TP2 {_f(tp2)} · R {rs.get('R_to_targets') or '—'}")
        if not rs.get("available"):
            lines.append(f"⚠ {rs.get('reason','')}")
    else:
        lines.append("Conditional: no valid location in the context direction — size/R unavailable.")
    # action
    if verdict.startswith("🟢"):
        lines.append("Action: deterministic sequence complete at a valid location — verify the 1m retest + brackets before executing.")
    elif verdict.startswith("🟡"):
        lines.append("Action: wait — location present, but trigger/room don't yet meet the A threshold. Don't enter on a touch/wick.")
    else:
        lines.append("Action: no trade — no preplanned location / sufficient room in the context direction.")
    lines.append("Tool only — not advice, not a forecast, not a profit guarantee.")
    return lines
