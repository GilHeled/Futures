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

import math
from datetime import datetime, timezone

from ict_v2 import market_structure as MS
from ict_v2 import order_blocks as OB
from ict_v2 import liquidity_runs as LR

# account rules (per account)
PER_TRADE_RISK = 150.0          # $ ceiling per account
POINT_VALUE = 2.0               # MNQ $/pt/contract
TICK = 0.25                     # MNQ tick — every displayed order price must be a multiple of this
MAX2_STOP_PTS = 37.5            # <= -> up to 2 contracts
MAX1_STOP_PTS = 75.0           # <= -> 1 contract; beyond -> no trade
MIN_R_A = 2.0                   # A needs >= 2R room
MIN_R_APLUS = 2.5              # A+ prefers >= 2.5R


def _c(b): return float(b["c"])
def _h(b): return float(b["h"])
def _l(b): return float(b["l"])
def _t(b): return b.get("t") if isinstance(b, dict) else None


def _tick(x, mode="near", tick=TICK):
    """Round a price to a valid tick. mode='up'/'down' round away from/ toward as the caller needs
    (stops round AWAY from entry so they stay beyond invalidation; targets round TOWARD entry so R is
    never overstated)."""
    if x is None:
        return None
    q = x / tick
    if mode == "up":
        q = math.ceil(q - 1e-9)
    elif mode == "down":
        q = math.floor(q + 1e-9)
    else:
        q = round(q)
    return round(q * tick, 2)


def _vol_unit(bars, n=20):
    """A volatility unit = median bar range over the last n bars (data-derived, not an invented number).
    Used as the minimum acceptable stop distance so a stop can't sit inside single-bar noise."""
    if not bars or len(bars) < 3:
        return None
    rng = sorted(abs(_h(b) - _l(b)) for b in bars[-n:])
    return round(rng[len(rng) // 2], 2)


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
        # every detected OB required a HH (bull) / LL (bear) displacement to form, and "active" means it
        # has NOT been closed through -> a VALID, still-live order block (not a mere last-opposite candle).
        cands.append({"type": "OB", "dir": "demand" if o.is_bull else "supply",
                      "top": round(o.top, 2), "bottom": round(o.bottom, 2), "ref": round(mid, 2),
                      "status": "active", "valid": True,
                      "confirmed_by": "higher-high displacement" if o.is_bull else "lower-low displacement",
                      "dist": abs(mid - price)})
    for r in runs:
        cands.append({"type": r.kind, "dir": "supply" if r.is_high else "demand",
                      "top": round(r.price, 2), "bottom": round(r.price, 2), "ref": round(r.price, 2),
                      "status": "unmitigated", "valid": True, "confirmed_by": r.kind,
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
    idl = in_dir[-1] if in_dir else None
    return {"completed": completed, "in_dir_events": len(in_dir), "events": len(e1),
            "last": None if not last else {"kind": last.kind, "dir": last.direction, "level": round(last.level, 2)},
            "in_dir_last": None if not idl else {"kind": idl.kind, "level": round(idl.level, 2),
                                                 "break_time": getattr(idl, "break_time", None)},
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


# ── sub-model: obstacle scan on the reward path (defined, causal, no look-ahead) ──
ACCEPT_CLOSES = 2   # >= this many CONSECUTIVE completed closes beyond a level = ACCEPTANCE.


def _acceptance(bars, idx, level, below):
    """CAUSAL status of a level formed at bar `idx`, using ONLY bars idx+1..end (all <= the snapshot —
    never a candle after 'now'). This defines what invalidates an obstacle, per the review:

      invalidated — >= ACCEPT_CLOSES CONSECUTIVE completed candles CLOSED beyond `level` (real body
                    acceptance through the zone). A single close, a wick, or an indicator 'mitigated'
                    flag is NOT sufficient on its own.
      weakened    — a wick pierced the level (low<level for support / high>level for resistance) but it
                    HELD on a close basis (no acceptance run). Still a live obstacle — not skipped.
      active      — price never traded beyond it since formation.

    Returns (status, evidence_text, ev_from_time, ev_to_time). `below=True` for a support (short path),
    False for a resistance (long path)."""
    run = 0
    run_from = None
    accepted = None
    wicked = False
    wick_t = None
    for b in bars[idx + 1:]:
        c = _c(b)
        pierced = (_l(b) < level) if below else (_h(b) > level)
        closed_beyond = (c < level) if below else (c > level)
        if pierced and not wicked:
            wicked = True
            wick_t = _t(b)
        if closed_beyond:
            run += 1
            if run == 1:
                run_from = _t(b)
            if run >= ACCEPT_CLOSES and accepted is None:
                accepted = (run_from, _t(b))
        else:
            run = 0
            run_from = None
    if accepted:
        return ("invalidated", f"accepted through: >= {ACCEPT_CLOSES} consecutive closes beyond {round(level, 2)}",
                accepted[0], accepted[1])
    if wicked:
        return ("weakened", "wick pierced but held on a close basis", wick_t, wick_t)
    return ("active", "untested since formation", None, None)


def obstacle_scan(series_by_tf, direction, entry, stop_pts):
    """Scan the reward path from the proposed entry outward and list EVERY opposing structure in order of
    encounter (nearest first), across 15m/5m/1m: active bullish/bearish OB boundaries, swing pivots, and
    LRLR/HRLR liquidity pools. Each row carries price, timeframe, why it opposes, its causal status
    (active/weakened/invalidated) with completed-candle evidence + timestamp, and distance/R from entry.

    The FIRST row that is not `invalidated` is the first active obstacle — we never skip a nearer obstacle
    to show a more attractive R at a farther one. Only the defined acceptance rule (`_acceptance`) may
    exclude a level, and only using candles at/before the snapshot."""
    if entry is None or not direction or not stop_pts:
        return {"rows": [], "first": None}
    short = direction == "SHORT"
    rows = []

    def add(tf, price, zone, kind, opposes, idx, accept_level):
        if short and not (price < entry):
            return
        if (not short) and not (price > entry):
            return
        status, ev, evf, evt = _acceptance(series_by_tf.get(tf) or [], idx, accept_level, below=short)
        rows.append({"tf": tf, "price": round(price, 2), "zone": zone, "kind": kind, "opposes": opposes,
                     "status": status, "evidence": ev, "ev_from": evf, "ev_to": evt,
                     "dist_pts": round(abs(entry - price), 2), "R": round(abs(entry - price) / stop_pts, 2)})

    for tf in ("15m", "5m", "1m"):
        bars = series_by_tf.get(tf) or []
        if not bars:
            continue
        for o in OB.detect_order_blocks(bars, swing_len=3, max_bars=100):
            if short and o.is_bull:                    # bull OB = demand support below a short
                add(tf, o.top, f"{round(o.bottom, 2)}–{round(o.top, 2)}", "bull OB", "demand zone",
                    o.left_index, o.bottom)            # accepted only when price closes below the zone floor
            elif (not short) and (not o.is_bull):      # bear OB = supply resistance above a long
                add(tf, o.bottom, f"{round(o.bottom, 2)}–{round(o.top, 2)}", "bear OB", "supply zone",
                    o.left_index, o.top)
        for p in MS.detect_pivots(bars, pivot_strength=3):
            if short and p["kind"] == "low":
                add(tf, p["price"], None, "swing low", "prior support", p["index"], p["price"])
            elif (not short) and p["kind"] == "high":
                add(tf, p["price"], None, "swing high", "prior resistance", p["index"], p["price"])
        for r in LR.detect_liquidity_runs(bars, tick=0.25):
            if short and (not r.is_high):               # SSL pool below = a liquidity draw/shelf
                add(tf, r.price, None, f"{r.kind} SSL", "liquidity shelf", r.pivot_index, r.price)
            elif (not short) and r.is_high:
                add(tf, r.price, None, f"{r.kind} BSL", "liquidity shelf", r.pivot_index, r.price)

    # dedupe by rounded price; keep the strongest label (prefer NOT-invalidated, prefer OB/zone), merge TFs
    order = {"active": 0, "weakened": 1, "invalidated": 2}
    best = {}
    for row in rows:
        k = row["price"]
        cur = best.get(k)
        if cur is None:
            best[k] = row
        else:
            cur["tf"] = ",".join(sorted(set(cur["tf"].split(",") + [row["tf"]])))
            if order[row["status"]] < order[cur["status"]] or (row["zone"] and not cur["zone"]):
                row["tf"] = cur["tf"]
                best[k] = row
    merged = sorted(best.values(), key=(lambda x: -x["price"]) if short else (lambda x: x["price"]))
    first = next((x for x in merged if x["status"] != "invalidated"), None)
    return {"rows": merged, "first": first}


# ── report assembly ──────────────────────────────────────────────────────────
def analyze(series_by_tf, symbol, *, price=None, now=None):
    """`now` (tz-aware datetime) overrides the freshness clock — pass the cursor time in a backtest so
    the freshness gate measures against the replayed moment, not wall-clock now."""
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
    vol5 = _vol_unit(b5) or 0.0              # median 5m bar range = volatility unit + min-stop floor
    stop_floor = round(vol5 * 0.5, 2)        # a stop tighter than half a 5m bar is noise
    buf = max(2.0, round(vol5 * 0.25, 2))    # volatility-aware buffer beyond the structure
    entry = stop = tp1 = tp2 = None
    direction = None
    stop_thesis = None
    sw = (ev or {}).get("sweep")
    if loc["nearest"]:
        nd = loc["nearest"]
        if nd["dir"] == "demand" and ctx["bias30"] in ("long", "neutral"):
            direction = "LONG"
            entry = nd["ref"]; stop = stop_level("LONG", nd, sw, buffer=buf)
            tp1 = ctx["external_bsl"][0] if ctx["external_bsl"] else None
            tp2 = ctx["external_bsl"][1] if len(ctx["external_bsl"]) > 1 else None
        elif nd["dir"] == "supply" and ctx["bias30"] in ("short", "neutral"):
            direction = "SHORT"
            entry = nd["ref"]; stop = stop_level("SHORT", nd, sw, buffer=buf)
            tp1 = ctx["external_ssl"][0] if ctx["external_ssl"] else None
            tp2 = ctx["external_ssl"][1] if len(ctx["external_ssl"]) > 1 else None
        if direction:
            # thesis is the 5m SETUP: invalidation beyond the 5m OB far-edge + swept liquidity
            stop_thesis = "5m-setup (beyond OB far-edge / swept liquidity)"
            # TICK-VALIDATE: every order price must be a 0.25 multiple. Stop rounds AWAY from entry so
            # it stays beyond invalidation; targets round TOWARD entry so R is never overstated.
            entry = _tick(entry, "near")
            if direction == "SHORT":
                stop = _tick(stop, "up"); tp1 = _tick(tp1, "up"); tp2 = _tick(tp2, "up")
            else:
                stop = _tick(stop, "down"); tp1 = _tick(tp1, "down"); tp2 = _tick(tp2, "down")

    tr = trigger(b1, direction)                          # direction-aware trigger
    rs = risk_size(entry, stop, [tp1, tp2])
    best_R = max(rs.get("R_to_targets") or [0]) if rs.get("R_to_targets") else None
    sp = rs.get("stop_pts")
    degenerate_stop = bool(sp is not None and stop_floor and sp < stop_floor)

    # FIRST ACTIVE obstacle on the reward path (15m/5m/1m), by order of encounter — never skip a nearer
    # obstacle to show a better R at a farther one. Only the defined causal acceptance rule excludes one.
    scan = obstacle_scan(series_by_tf, direction, entry, sp)
    fo = scan["first"]
    first_obst = fo["price"] if fo else None
    obstacle_R = fo["R"] if fo else None
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
            ref = now or datetime.now(timezone.utc)
            age = (ref - t).total_seconds()
            age_min = round(age / 60, 1)
            stale = age > 300
        except Exception:
            pass

    m1 = st.get("m1")
    conflict = bool(direction and m1 and (
        (direction == "LONG" and m1["dir"] == "bear") or (direction == "SHORT" and m1["dir"] == "bull")))
    sweep_side_ok = bool(sw and sw.get("side") == ("BSL" if direction == "SHORT" else "SSL"))
    sweep_reclaimed = bool(sweep_side_ok and sw and sw.get("mitigated"))
    trigger_ok = bool(tr["completed"])
    conf = loc.get("confluence", 0)
    # location confidence: a standalone conf-0 OB is a CANDIDATE, not confirmed 5m confluence. A timely
    # in-direction 1m trigger AT the location resolves it; otherwise it stays unresolved.
    loc_conf_ok = bool(loc.get("nearest") and (conf >= 1 or trigger_ok))
    room_ok = bool(effective_R is not None and effective_R >= MIN_R_A)
    loc_ok = bool(loc["nearest"] and direction and rs.get("available"))

    # ── GATES — each computed and reported SEPARATELY (staleness never hides the others) ──────
    gates = [
        ("stale data", not stale,
         (f"last bar {age_min} min old (run run-live.sh in market hours)" if stale else f"fresh ({age_min} min)")),
        ("valid location + sizeable stop", loc_ok,
         "no location in the context direction / stop not sizeable" if not loc_ok else "present"),
        ("structural stop ≥ volatility floor", not degenerate_stop,
         (f"stop {sp}pt < 5m floor {stop_floor}pt" if degenerate_stop else "ok")),
        ("location confidence (confirmed 5m confluence / timely 1m trigger)", loc_conf_ok,
         (f"standalone OB conf {conf}, no confirming trigger — CANDIDATE, unresolved" if not loc_conf_ok else "resolved")),
        ("liquidity raid completed (correct side + reclaimed)", sweep_reclaimed,
         ("no raid on the correct side yet" if not sweep_side_ok else
          (f"sweep {sw['price']} OPEN — not reclaimed" if sw and not sw.get("mitigated") else "reclaimed"))),
        ("1m trigger completed in direction", trigger_ok,
         "break→retest→second-break not completed our way" if not trigger_ok else "completed"),
        ("1m not opposing the side", not conflict,
         "1m structure still opposes (developing reversal)" if conflict else "aligned"),
        (f"≥ {MIN_R_A}R room before first active obstacle", room_ok,
         (f"only {effective_R}R before {_f(first_obst)} ({fo['kind']} {fo['tf']})" if (fo and not room_ok)
          else (f"{effective_R}R clear to target" if room_ok else "room unverified — no obstacle/target resolved"))),
    ]

    # ── STATE MACHINE: NO TRADE / WATCH / READY ──────────────────────────────
    hard = stale or (not loc_ok) or degenerate_stop         # cannot even be a WATCH candidate
    soft_ok = loc_conf_ok and sweep_reclaimed and trigger_ok and (not conflict) and room_ok
    pending = [f"{name}: {detail}" for name, ok, detail in gates if not ok]
    if hard:
        verdict, state = "🔴 No Trade", "NO_TRADE"
        reason = next(f"{name} — {detail}" for name, ok, detail in gates if not ok)
    elif soft_ok:
        verdict, state, reason = "🟢 Ready", "READY", "All gates pass at a valid location"
    else:
        verdict, state, reason = "🟡 Watch", "WATCH", next(
            f"{name} — {detail}" for name, ok, detail in gates
            if not ok and name not in ("stale data", "valid location + sizeable stop", "structural stop ≥ volatility floor"))

    lines = _report_lines(verdict, state, reason, symbol, price, direction, gr, ctx, loc, ev, st, tr,
                          entry, stop, rs, tp1, tp2, best_R, effective_R, first_obst, stop_thesis, pending, fo,
                          gates, scan)
    return {"verdict": verdict, "state": state, "reason": reason, "symbol": symbol, "price": round(price, 2),
            "direction": direction, "grade": gr.get("grade"), "context": ctx, "location": loc,
            "liquidity_event": ev, "structure": st, "trigger": tr, "risk": rs, "conflict": conflict,
            "pending": pending, "stop_thesis": stop_thesis, "degenerate_stop": degenerate_stop,
            "stop_floor": stop_floor, "vol_unit": vol5,
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "best_R": best_R,
            "first_obstacle": first_obst, "first_obstacle_detail": fo,
            "obstacle_R": obstacle_R, "effective_R": effective_R, "obstacle_scan": scan["rows"],
            "gates": [{"name": n, "ok": ok, "detail": d} for n, ok, d in gates],
            "stale": stale, "age_min": age_min, "flags": pending,
            "conditional": state != "READY", "lines": lines,
            "disclaimer": "Decision-support tool only — not advice, not a forecast, not a profit guarantee."}


def _f(x):
    return "—" if x is None else f"{x:,.2f}"


def _evt(t):
    """Short HH:MM from an ISO timestamp for evidence lines."""
    if not t:
        return "—"
    try:
        return datetime.fromisoformat(t).strftime("%m-%d %H:%M")
    except Exception:
        return str(t)


def _report_lines(verdict, state, reason, sym, price, direction, gr, ctx, loc, ev, st, tr, entry, stop, rs,
                  tp1, tp2, best_R, effective_R, first_obst, stop_thesis, pending=None, fo=None,
                  gates=None, scan=None):
    g = gr.get("grade") or ("pending" if state == "WATCH" else "—")
    nd = loc.get("nearest")
    conf = loc.get("confluence", 0)
    # OB status (valid/candidate/invalid) is a SEPARATE axis from confluence (stacked PD arrays).
    if not nd:
        locdesc = "—"
    else:
        status = nd.get("status", "?")
        by = nd.get("confirmed_by", "")
        valid = "valid" if nd.get("valid") else "candidate"
        confdesc = ("standalone — confluence 0 (no stacked PD array; does NOT count as A+ confluence)"
                    if conf == 0 else f"confluence {conf} (stacked PD arrays)")
        locdesc = (f"{nd['type']} {nd['dir']} @ {_f(nd['ref'])} — {status}/{valid}"
                   f"{f' (confirmed by {by})' if by else ''}; {confdesc}")
    sweep = ev.get("sweep")
    swdesc = "no recent sweep" if not sweep else f"sweep {sweep['side']} @ {_f(sweep['price'])} ({'reclaimed' if sweep['mitigated'] else 'OPEN — pending'})"
    m5 = st.get("m5"); m1 = st.get("m1")
    trg = "completed in trade direction" if tr.get("completed") else "PENDING (1m not shifted our way)"
    lines = [
        verdict,
        f"{sym} @ {_f(price)} · dir {direction or '—'} · grade {g} · (heuristic; verify on chart)",
        f"30m context: {ctx['bias30']} SCENARIO ({ctx.get('impulse') or '—'}; new leg NOT yet confirmed) · 15m obstacle: {ctx.get('obstacle15') or '—'}",
        f"External liquidity: BSL {ctx['external_bsl'] or '—'} · SSL {ctx['external_ssl'] or '—'} · EQH {ctx['eqh'] or '—'} EQL {ctx['eql'] or '—'}",
        f"5m location: {locdesc} · liquidity event: {swdesc}",
        f"Structure 5m: {m5 or '—'} · 1m: {m1 or '—'} · 1m trigger: {trg}",
    ]
    if state != "READY" and reason:
        lines.insert(2, "⚠ " + reason)
    plan_label = "PLAN" if state == "READY" else "Plan (pending trigger — provisional)"
    if entry is not None:
        contracts = rs.get("contracts", 0)
        lines.append(f"{plan_label}: entry ~{_f(entry)} · stop ~{_f(stop)} · {rs.get('stop_pts','—')}pt · "
                     f"{contracts} MNQ · risk ${rs.get('risk_per_account','—')}/account")
        if stop_thesis:
            lines.append(f"Invalidation thesis: {stop_thesis}")
        # ── ORDERED OBSTACLE TABLE (nearest first) — every candidate, its causal status + evidence ──
        rows = (scan or {}).get("rows") if isinstance(scan, dict) else scan
        if rows:
            lines.append("Obstacle scan (entry → target, nearest first):")
            mark = {"active": "●", "weakened": "◐", "invalidated": "○"}
            for r in rows[:8]:
                m = mark.get(r["status"], "?")
                z = f" [{r['zone']}]" if r.get("zone") else ""
                excl = "  ← EXCLUDED (accepted through)" if r["status"] == "invalidated" else \
                       ("  ← FIRST ACTIVE OBSTACLE" if first_obst is not None and r["price"] == first_obst else "")
                lines.append(f"  {m} {_f(r['price'])}{z} · {r['tf']} {r['kind']} · {r['status']} "
                             f"({r['evidence']}{'' if not r['ev_from'] else ' @ ' + _evt(r['ev_from'])}) · "
                             f"{r['R']}R{excl}")
        if fo:
            blocks = effective_R is not None and effective_R < MIN_R_A
            verd = (f"BLOCKS the A-setup — only {effective_R}R before it (< {MIN_R_A}R); B is not executable this phase"
                    if blocks else f"clears the ≥{MIN_R_A}R bar — {effective_R}R before it")
            lines.append(f"First active obstacle: {_f(first_obst)} ({fo['tf']} {fo['kind']}, {fo['status']}) · {verd}")
            if blocks:
                lines.append("  Advance ONLY on an OBSERVED close-through + acceptance below it, THEN re-assess a "
                             "new entry with fresh stop/size/targets/R. A future break never qualifies this entry.")
        else:
            lines.append(f"First active obstacle: none before target — {effective_R}R clear room to TP1")
        lines.append(f"TP1 {_f(tp1)} · TP2 {_f(tp2)} · R-to-target {rs.get('R_to_targets') or '—'} "
                     f"(PROVISIONAL — valid only after an actual 1m trigger; unblocked only past the obstacle)")
        if not rs.get("available"):
            lines.append(f"⚠ {rs.get('reason','')}")
    else:
        lines.append("No valid location in the context direction — size/R unavailable.")
    # ── GATES — every gate reported separately (staleness never hides the rest) ──
    if gates:
        lines.append("Gates:")
        for name, ok, detail in gates:
            lines.append(f"  {'✓' if ok else '✗'} {name}: {detail}")
    # action per state with the SPECIFIC reason (never a lumped catch-all)
    tag = {"READY": "READY", "WATCH": "WATCH", "NO_TRADE": "NO TRADE"}.get(state, state)
    lines.append(f"Action: {tag} — {reason}.")
    lines.append("Tool only — not advice, not a forecast, not a profit guarantee.")
    return lines
