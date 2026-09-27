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


def _o(b): return float(b["o"])
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


def trigger_threshold(b1, direction, price):
    """PRE-TRIGGER: the 1m level the trade must BREAK to arm — a trigger threshold, NOT a target obstacle.
    For a short it is the nearest 1m swing low below price (the low a bearish break must close under); for
    a long, the nearest swing high above. This is the level the trigger consumes, so it is deliberately
    kept OUT of the target-obstacle scan until the trigger completes."""
    if not direction or not b1:
        return None
    pivs = MS.detect_pivots(b1, pivot_strength=2)
    if direction == "SHORT":
        lows = [p for p in pivs if p["kind"] == "low" and p["price"] < price]
        p = max(lows, key=lambda x: x["price"]) if lows else None
    else:
        highs = [p for p in pivs if p["kind"] == "high" and p["price"] > price]
        p = min(highs, key=lambda x: x["price"]) if highs else None
    if not p:
        return None
    return {"level": p["price"], "time": p.get("time"),
            "must": ("close below to break" if direction == "SHORT" else "close above to break")}


TRIGGER_RECENCY = 8   # the second break must be within the last N 1m bars to count as a LIVE trigger.


def _cev(bars, idx, level, want):
    """Full completed-candle evidence for one event: OHLC, timestamp, the structure level tested, and
    whether the break was by CLOSE (body) or only a WICK. `want`='bear' tests a close/low below `level`."""
    if idx is None or not (0 <= idx < len(bars)):
        return None
    b = bars[idx]
    below = want == "bear"
    broke = ("close" if ((_c(b) < level) if below else (_c(b) > level))
             else ("wick" if ((_l(b) < level) if below else (_h(b) > level)) else "—"))
    return {"idx": idx, "t": _t(b), "o": _o(b), "h": _h(b), "l": _l(b), "c": _c(b),
            "level": round(level, 2), "broke": broke}


def trigger_sequence(b1, direction, buffer=2.0, recency=TRIGGER_RECENCY):
    """POST-TRIGGER: identify a COMPLETED, RECENT, GENUINE-continuation break → retest → second-break on
    finished 1m candles and build the plan FROM that event. `completed` requires ALL of:
      • a first in-direction structural break (close beyond the first swing level L1),
      • a RECENT second in-direction break (within `recency` bars — an old one is not a live trigger),
      • a retest pivot BETWEEN them (a pullback: lower-high for a short / higher-low for a long),
      • PROVEN CONTINUATION past L1 — after the retest the market makes a NEW extreme beyond L1 AND a
        completed candle CLOSES beyond L1 (for a short: a lower low below, and a close below, L1),
      • NO opposing structural break between the two (the leg was not reversed).
    A break + a retest + some later opposite-side level found "somewhere" is NOT enough — the second break
    must extend the move beyond the first low/high. If continuation is not proven the sequence is REJECTED
    (state stays PENDING) with `rejected_reason` + the candidate evidence, never silently marked complete.
    Entry = the broken swing level; stop = beyond the retest extreme. Everything uses candles ≤ snapshot."""
    want = {"LONG": "bull", "SHORT": "bear"}.get(direction)
    out = {"completed": False, "break": None, "retest": None, "second_break": None,
           "continuation": None, "entry": None, "stop": None, "thesis": None, "rejected_reason": None}
    if not want or not b1:
        out["rejected_reason"] = "no direction / no 1m data"
        return out
    n = len(b1)
    short = direction == "SHORT"
    ev = MS.detect_market_structure(b1, pivot_strength=2)
    direv = [e for e in ev if e.direction == want]
    oppdir = [e for e in ev if e.direction != want]
    pivs = MS.detect_pivots(b1, pivot_strength=2)
    if len(direv) < 2:
        out["rejected_reason"] = f"fewer than 2 in-direction 1m breaks (have {len(direv)})"
        return out
    recent = [e for e in direv if e.break_index >= n - recency]
    if not recent:
        out["rejected_reason"] = f"no in-direction 1m break in the last {recency} bars (not a live trigger)"
        return out
    e2 = recent[-1]                                        # continuation-break candidate (most recent)
    priors = [e for e in direv if e.break_index < e2.break_index]
    if not priors:
        out["rejected_reason"] = "no earlier break to form break→retest→continuation"
        return out
    e1 = priors[-1]                                        # the break immediately preceding
    ext = "high" if short else "low"
    mids = [p for p in pivs if p["kind"] == ext and e1.break_index < p["index"] < e2.break_index]
    retest = (max(mids, key=lambda p: p["price"]) if short else min(mids, key=lambda p: p["price"])) if mids else None
    # continuation past the FIRST broken level L1, measured only AFTER the retest (or after e1 if no retest)
    start = (retest["index"] if retest else e1.break_index) + 1
    seg = b1[start:e2.break_index + 1]
    if short:
        new_ext = min((_l(b) for b in seg), default=None)
        made_new = new_ext is not None and new_ext < e1.level          # a lower low below the first low
        closed_beyond = any(_c(b) < e1.level for b in seg)             # a close below the first low
    else:
        new_ext = max((_h(b) for b in seg), default=None)
        made_new = new_ext is not None and new_ext > e1.level
        closed_beyond = any(_c(b) > e1.level for b in seg)
    opp_between = any(e1.break_index < e.break_index < e2.break_index for e in oppdir)
    cont = {"first_level": round(e1.level, 2), "extreme_after_retest": (round(new_ext, 2) if new_ext is not None else None),
            "made_new_beyond_first": bool(made_new), "closed_beyond_first": bool(closed_beyond),
            "opposing_break_between": bool(opp_between)}
    brk = _cev(b1, e1.break_index, e1.level, want)
    sec = _cev(b1, e2.break_index, e2.level, want)
    rst = (_cev(b1, retest["index"], retest["price"], "bull" if short else "bear") if retest else None)
    out.update({"break": dict(brk, kind=e1.kind, time=e1.break_time) if brk else None,
                "retest": (dict(rst, price=round(retest["price"], 2), time=retest.get("time")) if rst else None),
                "second_break": dict(sec, kind=e2.kind, time=e2.break_time) if sec else None,
                "continuation": cont})
    if retest is None:
        out["rejected_reason"] = "no retest pivot between the two breaks"
        return out
    if opp_between:
        out["rejected_reason"] = "an opposing structural break occurred between the two breaks (leg reversed)"
        return out
    if not (made_new and closed_beyond):
        out["rejected_reason"] = (f"no continuation past the first {'low' if short else 'high'} "
                                  f"{round(e1.level, 2)} (extreme after retest "
                                  f"{cont['extreme_after_retest']}, close beyond={closed_beyond})")
        return out
    entry = round(e2.level, 2)
    stop = round(retest["price"] + buffer, 2) if short else round(retest["price"] - buffer, 2)
    out.update({"completed": True, "entry": entry, "stop": stop,
                "thesis": "1m break→retest→second-break, continuation past the first low/high "
                          "(stop beyond the retest extreme)"})
    return out


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


def _meaningful_obstacle(kind, tf):
    """Which levels count as a HARD barrier that gates the A-room test. Active order blocks (any TF),
    liquidity shelves (LRLR/HRLR/BSL/SSL pools) and HTF (5m/15m) swing structure are meaningful. A bare
    1m swing low/high is MINOR — shown for context but never the gating obstacle (don't treat every tiny
    1m swing as a target barrier)."""
    k = kind.lower()
    if "ob" in k:
        return True
    if "ssl" in k or "bsl" in k or "lrlr" in k or "hrlr" in k:
        return True
    if "swing" in k and tf in ("5m", "15m"):
        return True
    return False


def obstacle_scan(series_by_tf, direction, entry, stop_pts):
    """Scan the reward path from the proposed entry outward and list EVERY opposing structure in order of
    encounter (nearest first), across 15m/5m/1m: active bullish/bearish OB boundaries, swing pivots, and
    LRLR/HRLR liquidity pools. Each row carries price, timeframe, why it opposes, its causal status
    (active/weakened/invalidated) with completed-candle evidence + timestamp, distance/R, and whether it
    is a MEANINGFUL hard barrier (vs a minor 1m swing).

    The gating `first` is the first non-invalidated MEANINGFUL level — we never skip a nearer meaningful
    obstacle for a better R, and we never let a minor 1m swing masquerade as the barrier. Only the defined
    acceptance rule (`_acceptance`) excludes a level, using candles at/before the snapshot only."""
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
                     "meaningful": _meaningful_obstacle(kind, tf),
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
    # gating obstacle = first non-invalidated MEANINGFUL level (minor 1m swings are context, not barriers)
    first = next((x for x in merged if x["status"] != "invalidated" and x["meaningful"]), None)
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

    # ── DIRECTION from the 5m location + 30m context (NO entry/stop yet — those are set at the trigger) ─
    vol5 = _vol_unit(b5) or 0.0              # median 5m bar range = volatility unit
    vol1 = _vol_unit(b1) or 0.0              # median 1m bar range — the floor for a 1m-entry stop
    # the post-trigger entry is a 1m thesis, so its stop is floored by 1m (not 5m) volatility.
    stop_floor = round((vol1 or vol5) * 0.5, 2)   # a stop tighter than half a 1m bar is noise
    buf = max(2.0, round(vol1 * 0.25, 2)) if vol1 else max(2.0, round(vol5 * 0.25, 2))
    direction = None
    sw = (ev or {}).get("sweep")
    nd = loc.get("nearest")
    if nd:
        if nd["dir"] == "demand" and ctx["bias30"] in ("long", "neutral"):
            direction = "LONG"
        elif nd["dir"] == "supply" and ctx["bias30"] in ("short", "neutral"):
            direction = "SHORT"

    tr = trigger(b1, direction)                          # simple 1m shift (for the structure panel)
    threshold = trigger_threshold(b1, direction, price)  # PRE-trigger: the level that must break to arm
    trg = trigger_sequence(b1, direction, buffer=buf)    # POST-trigger: break→retest→second-break plan
    triggered = bool(trg["completed"])

    # ── TWO-PHASE LIFECYCLE ───────────────────────────────────────────────────
    # PRE-trigger: describe the setup + the trigger threshold; publish NO executable entry/stop/R.
    # POST-trigger: build the ENTIRE plan FROM the completed break→retest→second-break, then run the
    # obstacle + A-room test from that ACTUAL entry (never carry a provisional pre-trigger level forward).
    entry = stop = tp1 = tp2 = None
    stop_thesis = None
    scan = {"rows": [], "first": None}
    fo = None
    first_obst = obstacle_R = effective_R = best_R = sp = None
    rs = risk_size(None, None, None)
    degenerate_stop = False
    if triggered and direction:
        entry = trg["entry"]; stop = trg["stop"]; stop_thesis = trg["thesis"]
        if direction == "SHORT":
            tp1 = ctx["external_ssl"][0] if ctx["external_ssl"] else None
            tp2 = ctx["external_ssl"][1] if len(ctx["external_ssl"]) > 1 else None
        else:
            tp1 = ctx["external_bsl"][0] if ctx["external_bsl"] else None
            tp2 = ctx["external_bsl"][1] if len(ctx["external_bsl"]) > 1 else None
        entry = _tick(entry, "near")
        if direction == "SHORT":
            stop = _tick(stop, "up"); tp1 = _tick(tp1, "up"); tp2 = _tick(tp2, "up")
        else:
            stop = _tick(stop, "down"); tp1 = _tick(tp1, "down"); tp2 = _tick(tp2, "down")
        rs = risk_size(entry, stop, [tp1, tp2])
        best_R = max(rs.get("R_to_targets") or [0]) if rs.get("R_to_targets") else None
        sp = rs.get("stop_pts")
        degenerate_stop = bool(sp is not None and stop_floor and sp < stop_floor)
        scan = obstacle_scan(series_by_tf, direction, entry, sp)   # from the ACTUAL post-trigger entry
        fo = scan["first"]
        first_obst = fo["price"] if fo else None
        obstacle_R = fo["R"] if fo else None
        effective_R = obstacle_R if obstacle_R is not None else best_R

    # route that qualified the trigger — either a confirmed sweep+reclaim OR a structural rejection.
    route = ("sweep+reclaim" if (sw and sw.get("side") == ("BSL" if direction == "SHORT" else "SSL")
                                 and sw.get("mitigated"))
             else ("rejection+displacement (structural)" if triggered else None))

    gr = (grade(loc, route == "sweep+reclaim", True, effective_R) if triggered
          else {"grade": None, "trigger_completed": False, "why": "pre-trigger — no executable setup yet"})

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
    conf = loc.get("confluence", 0)
    alt_locs = max(0, (loc.get("count") or 0) - 1)          # other 5m locations available besides this one
    # location confidence: a standalone conf-0 OB is a CANDIDATE, not confirmed 5m confluence. A timely
    # in-direction trigger at the location resolves it; otherwise the engine notes if another 5m location exists.
    loc_conf_ok = bool(nd and (conf >= 1 or triggered))
    room_ok = bool(triggered and effective_R is not None and effective_R >= MIN_R_A)
    sizeable = bool(triggered and rs.get("available"))
    loc_ok = bool(nd and direction)                         # a valid 5m location in the context direction

    # ── GATES — reported SEPARATELY; ok=None means "n/a until the trigger" (never hides a real failure) ──
    thr = f"{_f(threshold['level'])}" if threshold else "—"
    gates = [
        ("stale data", not stale,
         (f"last bar {age_min} min old (run run-live.sh in market hours)" if stale else f"fresh ({age_min} min)")),
        ("valid 5m location in context direction", loc_ok,
         "no 5m location in the context direction" if not loc_ok else
         f"present ({nd['type']} {nd['dir']}{'' if alt_locs == 0 else f'; {alt_locs} other 5m location(s)'})"),
        ("location confidence (5m confluence / timely trigger)", loc_conf_ok,
         (f"standalone conf {conf} — CANDIDATE" + (f"; {alt_locs} other 5m location(s) exist" if alt_locs else "; no alternative 5m location")
          if not loc_conf_ok else ("confluence " + str(conf) if conf >= 1 else "resolved by timely trigger"))),
        ("1m not opposing the side", not conflict,
         "1m structure still opposes (developing reversal)" if conflict else "aligned"),
        ("trigger route qualified (sweep+reclaim OR rejection+displacement)", triggered,
         (f"{route}: break {trg['break']['level']} → retest {trg['retest']['level']} → second break {trg['second_break']['level']}"
          if triggered else
          f"PENDING — no completed break→retest→second-break yet; must break {thr} first" +
          (f" (sweep {sw['price']} OPEN — does not by itself qualify or block)" if (sw and not sw.get('mitigated')) else ""))),
        ("structural stop ≥ volatility floor", (None if not triggered else (not degenerate_stop)),
         ("deferred — set at trigger" if not triggered else (f"stop {sp}pt < 5m floor {stop_floor}pt" if degenerate_stop else "ok"))),
        ("permitted risk / sizeable stop", (None if not triggered else sizeable),
         ("deferred — set at trigger" if not triggered else (rs.get("reason") if not sizeable else f"{rs.get('contracts')} MNQ, ${rs.get('risk_per_account')}/acct"))),
        (f"≥ {MIN_R_A}R room before first meaningful obstacle", (None if not triggered else room_ok),
         ("deferred — measured from the post-trigger entry" if not triggered else
          (f"only {effective_R}R before {_f(first_obst)} ({fo['kind']} {fo['tf']})" if (fo and not room_ok)
           else (f"{effective_R}R clear to target" if room_ok else "room unverified")))),
    ]

    # ── STATE MACHINE: NO TRADE / WATCH / READY ──────────────────────────────
    hard = stale or (not loc_ok) or (triggered and (degenerate_stop or not sizeable))
    ready = bool(triggered and loc_ok and loc_conf_ok and (not conflict) and room_ok and sizeable and not stale)
    pending = [f"{name}: {detail}" for name, ok, detail in gates if ok is False]
    if hard:
        verdict, state = "🔴 No Trade", "NO_TRADE"
        reason = next(f"{name} — {detail}" for name, ok, detail in gates if ok is False)
    elif ready:
        verdict, state, reason = "🟢 Ready", "READY", f"Triggered plan at a valid location (route: {route})"
    elif not triggered:
        verdict, state = "🟡 Watch", "WATCH"
        reason = (f"Armed — awaiting the trigger (sweep+reclaim OR rejection+displacement) at 5m "
                  f"{nd['type']} {nd['dir']} @ {_f(nd['ref'])}; price must break {thr} first. "
                  f"Entry, stop, size and R are computed at the trigger — not now.")
    else:
        verdict, state, reason = "🟡 Watch", "WATCH", next(
            f"{name} — {detail}" for name, ok, detail in gates
            if ok is False and name not in ("stale data", "valid 5m location in context direction"))

    lines = _report_lines(verdict, state, reason, symbol, price, direction, gr, ctx, loc, ev, st, tr,
                          entry, stop, rs, tp1, tp2, best_R, effective_R, first_obst, stop_thesis, pending, fo,
                          gates, scan, threshold, trg, route, triggered)
    return {"verdict": verdict, "state": state, "reason": reason, "symbol": symbol, "price": round(price, 2),
            "direction": direction, "grade": gr.get("grade"), "context": ctx, "location": loc,
            "liquidity_event": ev, "structure": st, "trigger": tr, "risk": rs, "conflict": conflict,
            "triggered": triggered, "trigger_sequence": trg, "trigger_threshold": threshold, "route": route,
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
                  gates=None, scan=None, threshold=None, trg=None, route=None, triggered=False):
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
    # 1m TRIGGER SEQUENCE status (break→retest→second break), not a mere shift
    if triggered and trg:
        seqdesc = (f"COMPLETED via {route} — break {_f(trg['break']['level'])} → retest {_f(trg['retest']['level'])} "
                   f"→ second break {_f(trg['second_break']['level'])} (continuation past the first level)")
    else:
        rej = (trg or {}).get("rejected_reason")
        base = (f"PENDING — must break {_f(threshold['level'])} first (trigger threshold, NOT a target obstacle)"
                if threshold else "PENDING — no in-direction 1m break yet")
        seqdesc = base + (f" · candidate rejected: {rej}" if rej else "")
    lines = [
        verdict,
        f"{sym} @ {_f(price)} · dir {direction or '—'} · grade {g} · (heuristic; verify on chart)",
        f"30m context: {ctx['bias30']} SCENARIO ({ctx.get('impulse') or '—'}; new leg NOT yet confirmed) · 15m obstacle: {ctx.get('obstacle15') or '—'}",
        f"External liquidity: BSL {ctx['external_bsl'] or '—'} · SSL {ctx['external_ssl'] or '—'} · EQH {ctx['eqh'] or '—'} EQL {ctx['eql'] or '—'}",
        f"5m location: {locdesc} · liquidity event: {swdesc}",
        f"Structure 5m: {m5 or '—'} · 1m: {m1 or '—'}",
        f"1m trigger: {seqdesc}",
    ]
    if state != "READY" and reason:
        lines.insert(2, "⚠ " + reason)
    if not triggered:
        # PRE-TRIGGER: no executable plan. Describe the setup + the threshold; defer entry/stop/size/R.
        if threshold:
            lines.append(f"Trigger threshold (must break to arm): {_f(threshold['level'])} — {threshold.get('must','')}. "
                         f"This is a TRIGGER level, not the trade's first target obstacle.")
        # if a candidate sequence was found but REJECTED, show the evidence (no silent skipping)
        rj = (trg or {}).get("rejected_reason")
        if rj and (trg or {}).get("break"):
            b, r, s, c = trg.get("break"), trg.get("retest"), trg.get("second_break"), trg.get("continuation") or {}
            lines.append(f"Trigger candidate REJECTED → {rj}:")
            if b:
                lines.append(f"  break @ {_evt(b['t'])} {b.get('kind','')} of {_f(b['level'])} · O{_f(b['o'])} H{_f(b['h'])} L{_f(b['l'])} C{_f(b['c'])} ({b['broke']})")
            if r:
                lines.append(f"  retest @ {_evt(r['t'])} pivot {_f(r.get('price'))} · O{_f(r['o'])} H{_f(r['h'])} L{_f(r['l'])} C{_f(r['c'])}")
            if s:
                lines.append(f"  2nd break @ {_evt(s['t'])} {s.get('kind','')} of {_f(s['level'])} · O{_f(s['o'])} H{_f(s['h'])} L{_f(s['l'])} C{_f(s['c'])} ({s['broke']})")
            lines.append(f"  continuation: extreme after retest {_f(c.get('extreme_after_retest'))} vs first level "
                         f"{_f(c.get('first_level'))} · closed beyond first={c.get('closed_beyond_first')} · "
                         f"opposing break between={c.get('opposing_break_between')}")
        lines.append("Plan: DEFERRED — entry, stop, size, first obstacle and R are computed on the completed "
                     "break→retest→second-break, from that actual entry (no provisional levels carried forward).")
        tag = {"READY": "READY", "WATCH": "WATCH", "NO_TRADE": "NO TRADE"}.get(state, state)
        if gates:
            lines.append("Gates:")
            for name, ok, detail in gates:
                lines.append(f"  {'✓' if ok else ('✗' if ok is False else '◔')} {name}: {detail}")
        lines.append(f"Action: {tag} — {reason}")
        lines.append("Tool only — not advice, not a forecast, not a profit guarantee.")
        return lines
    plan_label = "PLAN" if state == "READY" else "Plan (post-trigger)"
    if entry is not None:
        contracts = rs.get("contracts", 0)
        lines.append(f"{plan_label}: entry ~{_f(entry)} (retest of broken 1m level) · stop ~{_f(stop)} "
                     f"(beyond retest {_f((trg or {}).get('retest',{}).get('level'))}) · {rs.get('stop_pts','—')}pt · "
                     f"{contracts} MNQ · risk ${rs.get('risk_per_account','—')}/account")
        if stop_thesis:
            lines.append(f"Invalidation thesis: {stop_thesis}")
        # ── ORDERED OBSTACLE TABLE (nearest first) — every candidate, its causal status + evidence ──
        rows = (scan or {}).get("rows") if isinstance(scan, dict) else scan
        if rows:
            lines.append("Obstacle scan (post-trigger entry → target, nearest first):")
            mark = {"active": "●", "weakened": "◐", "invalidated": "○"}
            for r in rows[:8]:
                m = mark.get(r["status"], "?")
                z = f" [{r['zone']}]" if r.get("zone") else ""
                tag = ""
                if r["status"] == "invalidated":
                    tag = "  ← EXCLUDED (accepted through)"
                elif first_obst is not None and r["price"] == first_obst:
                    tag = "  ← FIRST MEANINGFUL OBSTACLE"
                elif not r.get("meaningful"):
                    tag = "  (minor 1m — not a hard barrier)"
                lines.append(f"  {m} {_f(r['price'])}{z} · {r['tf']} {r['kind']} · {r['status']} "
                             f"({r['evidence']}{'' if not r['ev_from'] else ' @ ' + _evt(r['ev_from'])}) · "
                             f"{r['R']}R{tag}")
        if fo:
            blocks = effective_R is not None and effective_R < MIN_R_A
            verd = (f"BLOCKS the A-setup — only {effective_R}R before it (< {MIN_R_A}R); B is not executable this phase"
                    if blocks else f"clears the ≥{MIN_R_A}R bar — {effective_R}R before it")
            lines.append(f"First meaningful obstacle: {_f(first_obst)} ({fo['tf']} {fo['kind']}, {fo['status']}) · {verd}")
            if blocks:
                lines.append("  Advance ONLY on an OBSERVED close-through + acceptance below it, THEN re-assess a "
                             "new entry with fresh stop/size/targets/R. A future break never qualifies this entry.")
        else:
            lines.append(f"First meaningful obstacle: none before target — {effective_R}R clear room to TP1")
        lines.append(f"TP1 {_f(tp1)} · TP2 {_f(tp2)} · R-to-target {rs.get('R_to_targets') or '—'} "
                     f"(unblocked only past the first meaningful obstacle)")
        if not rs.get("available"):
            lines.append(f"⚠ {rs.get('reason','')}")
    else:
        lines.append("No valid location in the context direction — size/R unavailable.")
    # ── GATES — every gate reported separately (staleness never hides the rest) ──
    if gates:
        lines.append("Gates:")
        for name, ok, detail in gates:
            lines.append(f"  {'✓' if ok else ('✗' if ok is False else '◔')} {name}: {detail}")
    # action per state with the SPECIFIC reason (never a lumped catch-all)
    tag = {"READY": "READY", "WATCH": "WATCH", "NO_TRADE": "NO TRADE"}.get(state, state)
    lines.append(f"Action: {tag} — {reason}.")
    lines.append("Tool only — not advice, not a forecast, not a profit guarantee.")
    return lines
