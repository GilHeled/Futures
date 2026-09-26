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
    # distinct levels only (30m + 15m can surface the same swing) so TP1/TP2 are different targets
    bsl = sorted({round(r.price, 2) for r in unmit if r.is_high and r.price > price})
    ssl = sorted({round(r.price, 2) for r in unmit if (not r.is_high) and r.price < price}, reverse=True)
    eqh = sorted({round(r.price, 2) for r in unmit if r.is_high and r.kind == "LRLR"})
    eql = sorted({round(r.price, 2) for r in unmit if (not r.is_high) and r.kind == "LRLR"})
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
def trigger(b1, trade_dir=None):
    """1m trigger, DIRECTION-AWARE. `completed` requires the most recent CONFIRMED 1m structure shift
    to be in the trade direction (a bearish shift for a short, bullish for a long) — the minimal proof
    the 1m is actually breaking our way. Merely counting structure events (any direction, anywhere in
    the window) is NOT a trigger; the retest stays discretionary and is never auto-confirmed."""
    e1 = MS.detect_market_structure(b1, pivot_strength=2)
    want = {"LONG": "bull", "SHORT": "bear"}.get(trade_dir)
    in_dir = [e for e in e1 if e.direction == want] if want else []
    last = e1[-1] if e1 else None
    completed = bool(want and last and last.direction == want)   # latest 1m shift is in OUR direction
    return {"completed": completed, "in_dir_events": len(in_dir), "events": len(e1),
            "last": None if not last else {"kind": last.kind, "dir": last.direction, "level": round(last.level, 2)},
            "note": "1m must be shifting in the trade direction; retest is discretionary (verify on chart)"}


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
def grade(loc, sweep_side_ok, trigger_ok, room_R):
    """Grade the EXECUTABLE setup. A/A+/B require a COMPLETED in-direction trigger; without it the
    location may be valid but the state is WATCH, never a graded entry. `room_R` is R to the FIRST
    opposing obstacle (not the far target)."""
    if not loc.get("nearest"):
        return {"grade": None, "trigger_completed": False, "why": "no preplanned location"}
    if not trigger_ok:
        return {"grade": None, "trigger_completed": False, "why": "location present, trigger pending (WATCH)"}
    if room_R is not None and room_R < 1.5:
        return {"grade": None, "trigger_completed": True, "why": f"first-obstacle room {room_R}R < 1.5"}
    if sweep_side_ok and room_R and room_R >= MIN_R_APLUS and loc.get("confluence", 0) >= 1:
        g = "A+"
    elif room_R and room_R >= MIN_R_A:
        g = "A"
    elif room_R and room_R >= 1.5:
        g = "B"
    else:
        g = None
    return {"grade": g, "trigger_completed": True, "why": "location+trigger+first-obstacle-room"}


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

    # PROVISIONAL entry proposal from the nearest 5m location (never a live fill). Numbers are only
    # presented as an executable plan in the READY state; in WATCH they are pending estimates.
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

    tr = trigger(b1, direction)                          # direction-aware trigger
    rs = risk_size(entry, stop, [tp1, tp2])
    best_R = max(rs.get("R_to_targets") or [0]) if rs.get("R_to_targets") else None

    # first OPPOSING obstacle between entry and target — R is measured to it, not the far target
    first_obst = None
    if entry is not None:
        obs5 = [o for o in OB.detect_order_blocks(b5, swing_len=3, max_bars=100) if o.state == "active"]
        if direction == "SHORT":
            below = [o.top for o in obs5 if o.is_bull and o.top < entry]      # demand below caps a short
            first_obst = max(below) if below else None
        elif direction == "LONG":
            above = [o.bottom for o in obs5 if (not o.is_bull) and o.bottom > entry]   # supply above caps a long
            first_obst = min(above) if above else None
    obstacle_R = None
    sp = rs.get("stop_pts")
    if first_obst is not None and sp:
        obstacle_R = round(abs(entry - first_obst) / sp, 2)
    effective_R = obstacle_R if obstacle_R is not None else best_R

    gr = grade(loc, sw is not None and sw.get("side") == ("BSL" if direction == "SHORT" else "SSL"),
               tr["completed"], effective_R)

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
            stale = age > 300
        except Exception:
            pass

    m1 = st.get("m1")
    conflict = bool(direction and m1 and (
        (direction == "LONG" and m1["dir"] == "bear") or (direction == "SHORT" and m1["dir"] == "bull")))
    sweep_side_ok = bool(sw and sw.get("side") == ("BSL" if direction == "SHORT" else "SSL"))
    trigger_ok = bool(tr["completed"])

    # ── STATE MACHINE: NO TRADE / WATCH / READY ──────────────────────────────
    # A condition that is open / unverified / conf-0 / opposite-structure counts as PENDING, never done.
    pending = []
    verdict = "🔴 No Trade"; state = "NO_TRADE"
    if stale:
        pending.append(f"Data not live (last update {age_min} min ago) — run run-live.sh during market hours")
    elif not (loc["nearest"] and direction and rs.get("available")):
        pending.append("No valid location / sizeable stop in the context direction")
    elif effective_R is not None and effective_R < 1.5:
        pending.append(f"Inadequate room — first obstacle {first_obst} is only {effective_R}R away")
    else:
        if conflict:
            pending.append("1m structure still opposes the proposed side (anticipated reversal, not confirmed)")
        if not sweep_side_ok:
            pending.append("No confirmed liquidity raid on the correct side (sweep absent / wrong side)")
        elif sw and not sw.get("mitigated"):
            pending.append(f"Sweep {sw['price']} is OPEN (not reclaimed) — pending, not a completed event")
        if not trigger_ok:
            pending.append("1m trigger not completed in the trade direction (break→retest→break pending)")
        if effective_R is not None and effective_R < MIN_R_A:
            pending.append(f"First-obstacle room only {effective_R}R (< {MIN_R_A}R)")
        if not pending:
            verdict, state = "🟢 Ready", "READY"
        else:
            verdict, state = "🟡 Watch", "WATCH"

    lines = _report_lines(verdict, state, symbol, price, direction, gr, ctx, loc, ev, st, tr, entry, stop, rs,
                          tp1, tp2, best_R, effective_R, first_obst, pending)
    return {"verdict": verdict, "state": state, "symbol": symbol, "price": round(price, 2), "direction": direction,
            "grade": gr.get("grade"), "context": ctx, "location": loc, "liquidity_event": ev,
            "structure": st, "trigger": tr, "risk": rs, "conflict": conflict, "pending": pending,
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "best_R": best_R,
            "first_obstacle": first_obst, "obstacle_R": obstacle_R, "effective_R": effective_R,
            "stale": stale, "age_min": age_min, "flags": pending,
            "conditional": state != "READY", "lines": lines,
            "disclaimer": "Decision-support tool only — not advice, not a forecast, not a profit guarantee."}


def _f(x):
    return "—" if x is None else f"{x:,.2f}"


def _report_lines(verdict, state, sym, price, direction, gr, ctx, loc, ev, st, tr, entry, stop, rs,
                  tp1, tp2, best_R, effective_R, first_obst, pending=None):
    g = gr.get("grade") or ("pending" if state == "WATCH" else "—")
    nd = loc.get("nearest")
    locdesc = "—" if not nd else f"{nd['type']} {nd['dir']} @ {_f(nd['ref'])} (confluence {loc['confluence']})"
    sweep = ev.get("sweep")
    swdesc = "no recent sweep" if not sweep else f"sweep {sweep['side']} @ {_f(sweep['price'])} ({'reclaimed' if sweep['mitigated'] else 'OPEN — pending'})"
    m5 = st.get("m5"); m1 = st.get("m1")
    trg = "completed in trade direction" if tr.get("completed") else "PENDING (1m not shifted our way)"
    lines = [
        verdict,
        f"{sym} @ {_f(price)} · dir {direction or '—'} · grade {g} · (heuristic; verify on chart)",
        f"30m context: bias {ctx['bias30']} · {ctx.get('impulse') or '—'} · 15m obstacle: {ctx.get('obstacle15') or '—'}",
        f"External liquidity: BSL {ctx['external_bsl'] or '—'} · SSL {ctx['external_ssl'] or '—'} · EQH {ctx['eqh'] or '—'} EQL {ctx['eql'] or '—'}",
        f"5m location: {locdesc} · liquidity event: {swdesc}",
        f"Structure 5m: {m5 or '—'} · 1m: {m1 or '—'} · 1m trigger: {trg}",
    ]
    for fl in reversed(pending or []):
        lines.insert(2, "⚠ " + fl)
    # plan — only an executable plan in READY; otherwise a pending sketch
    plan_label = "PLAN" if state == "READY" else "Plan (pending trigger — provisional)"
    if entry is not None:
        contracts = rs.get("contracts", 0)
        lines.append(f"{plan_label}: entry ~{_f(entry)} · stop ~{_f(stop)} · {rs.get('stop_pts','—')}pt · "
                     f"{contracts} MNQ · risk ${rs.get('risk_per_account','—')}/account")
        obs = "" if first_obst is None else f" · first obstacle {_f(first_obst)} ({effective_R}R)"
        lines.append(f"TP1 {_f(tp1)} · TP2 {_f(tp2)} · R-to-target {rs.get('R_to_targets') or '—'}{obs}")
        if not rs.get("available"):
            lines.append(f"⚠ {rs.get('reason','')}")
    else:
        lines.append("No valid location in the context direction — size/R unavailable.")
    # action per state
    if state == "READY":
        lines.append("Action: READY — required events completed at a valid location; verify brackets and execute per your plan.")
    elif state == "WATCH":
        lines.append("Action: WATCH — location exists but the conditions above are still pending. Do not enter until they complete.")
    else:
        lines.append("Action: NO TRADE — invalidated, stale, or inadequate room.")
    lines.append("Tool only — not advice, not a forecast, not a profit guarantee.")
    return lines
