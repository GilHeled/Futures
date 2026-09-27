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

CONFIG_VERSION = "2026-09-27.spec-v1"

# ── APPROVED account rules (user-mandated; per account) ───────────────────────
PER_TRADE_RISK = 150.0          # $ ceiling per account (APPROVED)
POINT_VALUE = 2.0               # MNQ $/pt/contract (APPROVED — instrument fact)
TICK = 0.25                     # MNQ tick — every displayed order price must be a multiple (APPROVED)
MAX2_STOP_PTS = 37.5            # <= -> up to 2 contracts at $150 (APPROVED, derived from $150/$2/2)
MAX1_STOP_PTS = 75.0            # <= -> 1 contract; beyond -> no trade (APPROVED)
MAX_CONTRACTS = 2               # plan permits ONE or TWO MNQ; three is disallowed (APPROVED)
MIN_R_A = 2.0                   # A requires >= 2R credible room before the first obstacle (APPROVED)
MIN_R_B = 1.5                   # B first-objective floor (APPROVED, watch-only this phase)
DAILY_PDLL = 300.0             # per-account personal daily loss limit, Liquidate & Block (APPROVED)
DLL = 1000.0                    # per-account daily loss limit (APPROVED)

# ── PROVISIONAL engineering heuristics (NOT user-approved, NOT "derived from data") ───────────────
# Each: {value, rationale, range, unit}. They are safety checks and must NOT override a valid
# structural setup; they are configurable and require separate evidence before being treated as truth.
CONFIG = {
    "MIN_R_APLUS": {"value": 2.5, "unit": "R", "range": [2.0, 3.5],
                    "rationale": "A+ clean-path preference; a GRADING CONVENTION to validate, not a mandate"},
    "TRIGGER_RECENCY": {"value": 8, "unit": "1m bars", "range": [3, 20],
                        "rationale": "second break must be recent to be a LIVE trigger; provisional window"},
    "STOP_BUFFER_MULT": {"value": 0.25, "unit": "x median 1m range", "range": [0.0, 1.0],
                         "rationale": "tick-valid buffer beyond the retest extreme; provisional"},
    "STOP_FLOOR_MULT": {"value": 0.5, "unit": "x median 1m range", "range": [0.0, 1.0],
                        "rationale": "reject sub-noise stops; a SAFETY CHECK, not structural invalidation"},
    "ACCEPT_CLOSES": {"value": 2, "unit": "consecutive closes", "range": [1, 4],
                      "rationale": "clear a level heuristically; NOT the sole clearance test — evidence also weighed"},
    "STALE_SECONDS": {"value": 300, "unit": "seconds", "range": [60, 3600],
                      "rationale": "freshness window; should vary by session/closure (provisional default)"},
    "FEE_SLIPPAGE_PER_CONTRACT": {"value": 1.5, "unit": "$/contract/side round-trip est.", "range": [0.0, 10.0],
                                  "rationale": "fee+slippage reserve for daily-budget & backtest; provisional estimate"},
    "ORDER_TTL_BARS": {"value": 30, "unit": "1m bars", "range": [5, 120],
                       "rationale": "prospective-limit time-to-live before cancel; provisional"},
    "BACKTEST_MAX_HOLD_BARS": {"value": 240, "unit": "1m bars", "range": [30, 1440],
                               "rationale": "backtest timeout only; NOT a trade-management rule"},
}


def cfg(name):
    """Read a provisional heuristic's current value (see CONFIG for rationale/range/default)."""
    return CONFIG[name]["value"]


def rule_inventory():
    """The three-category provenance inventory the spec requires: APPROVED rules, PROVISIONAL heuristics
    (named, with rationale/range), and the OBSERVED evidence classes cited in each decision."""
    return {
        "config_version": CONFIG_VERSION,
        "approved": {
            "per_trade_risk_usd": PER_TRADE_RISK, "point_value": POINT_VALUE, "tick": TICK,
            "max2_stop_pts": MAX2_STOP_PTS, "max1_stop_pts": MAX1_STOP_PTS, "max_contracts": MAX_CONTRACTS,
            "min_R_A": MIN_R_A, "min_R_B": MIN_R_B, "daily_pdll": DAILY_PDLL, "dll": DLL,
            "timeframe_roles": "30m context · 5m location · 1m trigger; a 1m shift cannot set a 30m/5m thesis",
            "grade_permission": "A/A+ eligible; B watch-only; C no-trade (initial phase, 1 trade/day)",
            "two_R_A": "A requires >=2R credible room before the first meaningful obstacle",
        },
        "provisional": {k: {kk: vv for kk, vv in v.items()} for k, v in CONFIG.items()},
        "observed_evidence_classes": [
            "OHLC + completed timestamps", "indicator events (OB/MSS/BOS/HRLR) + their source candles",
            "tested/mitigated zone history", "actual fills + account state (when supplied)",
        ],
        "unavailable_in_this_env": [
            "live account balances / copier / bracket / PDLL state",
            "live macro-news calendar (Asia/Jerusalem window)", "live position management / fills",
        ],
    }


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
    # BIAS is a labeled CONTEXT (bullish/bearish/mixed/unknown) with evidence + invalidation — NOT a hard
    # long-only/short-only permission. A single most-recent BOS/MSS is only tentative; two agreeing events
    # make a directional bias; two disagreeing = mixed. (spec §3)
    impulse = None
    bias, bias_conf = "unknown", "none"
    if last30:
        impulse = "extending (BOS)" if last30.kind == "BOS" else "shift MSS (correction→new)"
        d2 = [e.direction for e in ev30[-2:]]
        if len(d2) >= 2 and d2[-1] == d2[-2]:
            bias = "bullish" if d2[-1] == "bull" else "bearish"
            bias_conf = "two agreeing 30m events"
        else:
            bias = "bullish" if last30.direction == "bull" else "bearish"
            bias_conf = "single 30m event — tentative"
        # if the two most recent events disagree it is genuinely mixed
        if len(d2) >= 2 and d2[-1] != d2[-2]:
            bias, bias_conf = "mixed", "last two 30m events disagree"
    bias_invalidation = None if not last30 else round(last30.level, 2)
    bias_evidence = [{"kind": e.kind, "dir": e.direction, "level": round(e.level, 2), "time": e.break_time}
                     for e in ev30[-3:]]
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
    return {"bias30": bias, "bias_confidence": bias_conf, "bias_invalidation": bias_invalidation,
            "bias_evidence": bias_evidence, "impulse": impulse, "heuristic": True,
            "external_bsl": [round(x, 2) for x in bsl[:3]], "external_ssl": [round(x, 2) for x in ssl[:3]],
            "eqh": eqh[:3], "eql": eql[:3], "htf_ob_30m": htf_ob[:4],
            "events30": MS.summary(ev30)["total"], "obstacle15": _obstacle15(ev15, price)}


def _obstacle15(ev15, price):
    # nearest opposing 15m structure level that could cap reward
    if not ev15:
        return None
    e = ev15[-1]
    return {"kind": e.kind, "dir": e.direction, "level": round(e.level, 2)}


# ── sub-model: actionable 5m location (QUALITY-scored, not merely nearest) ────
def location(b5, price):
    """Select the 5m location by QUALITY + relevance, not geographic nearness (spec §3). A meaningful OB
    is the final opposing area preceding a displacement that BROKE 5m structure; an HRLR sweep is a strong
    location; a bare unmitigated pivot / LRLR is weaker. `quality` scores this; the selected location is
    the highest-quality one (proximity only breaks ties). Being the last opposite candle or any new HH/LL
    is NOT sufficient — that shows as low quality / candidate. Imbalance(FVG)-only OBs are not scored here
    (a known limitation, disclosed)."""
    obs = [o for o in OB.detect_order_blocks(b5, swing_len=3, max_bars=100) if o.state == "active"]
    runs = [r for r in LR.detect_liquidity_runs(b5, tick=0.25) if not r.mitigated]
    ev5 = MS.detect_market_structure(b5, pivot_strength=3)
    vu = _vol_unit(b5) or 1.0
    cands = []
    for o in obs:
        mid = (o.top + o.bottom) / 2
        want = "bull" if o.is_bull else "bear"
        broke = any(e.direction == want and o.left_index < e.break_index <= o.left_index + 12 for e in ev5)
        score, reasons = 1, []
        if broke:
            score += 2; reasons.append("displacement broke 5m structure")
        else:
            reasons.append("no 5m structure break at formation — candidate only")
        dist = abs(mid - price)
        if dist <= 2 * vu:
            score += 1; reasons.append("near current price")
        src = b5[o.left_index] if 0 <= o.left_index < len(b5) else None
        cands.append({"type": "OB", "dir": "demand" if o.is_bull else "supply",
                      "top": round(o.top, 2), "bottom": round(o.bottom, 2), "ref": round(mid, 2),
                      "status": "active", "valid": True, "meaningful": bool(broke),
                      "confirmed_by": "higher-high displacement" if o.is_bull else "lower-low displacement",
                      "left_index": o.left_index, "left_time": o.left_time,
                      "src_candle": (None if not src else {"o": float(src["o"]), "h": float(src["h"]),
                                                           "l": float(src["l"]), "c": float(src["c"]), "t": src.get("t")}),
                      "score": score, "quality_reason": "; ".join(reasons), "dist": dist})
    for r in runs:
        score, reasons = 1, []
        if r.kind == "HRLR":
            score += 2; reasons.append("HRLR sweep (strong location)")
        elif r.kind == "LRLR":
            score += 1; reasons.append("LRLR equal-level pool")
        else:
            reasons.append("plain unmitigated pivot — weak")
        dist = abs(r.price - price)
        if dist <= 2 * vu:
            score += 1; reasons.append("near current price")
        cands.append({"type": r.kind, "dir": "supply" if r.is_high else "demand",
                      "top": round(r.price, 2), "bottom": round(r.price, 2), "ref": round(r.price, 2),
                      "status": "unmitigated", "valid": True, "meaningful": r.kind in ("HRLR", "LRLR"),
                      "confirmed_by": r.kind, "left_index": getattr(r, "pivot_index", None),
                      "left_time": getattr(r, "pivot_time", None),
                      "score": score, "quality_reason": "; ".join(reasons) or "weak", "dist": dist})
    # SELECT by quality first, proximity only as a tie-break (never the primary criterion)
    cands.sort(key=lambda c: (-c["score"], c["dist"]))
    nearest = cands[0] if cands else None
    conf = 0
    if nearest:
        conf = sum(1 for c in cands if abs(c["ref"] - nearest["ref"]) <= 3.0) - 1
    return {"nearest": nearest, "confluence": conf, "count": len(cands), "heuristic": True,
            "quality": (nearest["score"] if nearest else None),
            "quality_reason": (nearest["quality_reason"] if nearest else "no 5m location"),
            "candidates": [{"type": c["type"], "dir": c["dir"], "ref": c["ref"], "score": c["score"]} for c in cands[:6]]}


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


TRIGGER_RECENCY = cfg("TRIGGER_RECENCY")   # PROVISIONAL (see CONFIG): recency window for a LIVE second break.


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
    # CONTINUATION (spec §4): "actual continuation in the trade direction" = after the retest the market
    # extends PAST the pre-retest extreme (a lower low after a short's pullback / higher high after a long's).
    # This is NOT the over-strict "beat the very first extreme" rule, and NOT a mere ordering coincidence.
    # We state which swing e2 broke and require: (retest exists) + (no opposing break between) + (a lower
    # low / higher high after the retest than before it).
    opp_between = any(e1.break_index < e.break_index < e2.break_index for e in oppdir)
    pre_ext = post_ext = continued = None
    if retest:
        r_idx = retest["index"]
        pre_seg = b1[e1.break_index:r_idx + 1]
        post_seg = b1[r_idx:e2.break_index + 1]
        if short:
            pre_ext = min((_l(b) for b in pre_seg), default=None)
            post_ext = min((_l(b) for b in post_seg), default=None)
            continued = (pre_ext is not None and post_ext is not None and post_ext < pre_ext)
        else:
            pre_ext = max((_h(b) for b in pre_seg), default=None)
            post_ext = max((_h(b) for b in post_seg), default=None)
            continued = (pre_ext is not None and post_ext is not None and post_ext > pre_ext)
    cont = {"first_level": round(e1.level, 2), "second_break_swing_level": round(e2.level, 2),
            "second_break_from_index": e2.from_index, "retest_index": (retest["index"] if retest else None),
            "pre_retest_extreme": (round(pre_ext, 2) if pre_ext is not None else None),
            "post_retest_extreme": (round(post_ext, 2) if post_ext is not None else None),
            "continued_past_retest": bool(continued), "opposing_break_between": bool(opp_between)}
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
    if not continued:
        out["rejected_reason"] = (f"no continuation after the retest (post-retest extreme {cont['post_retest_extreme']} "
                                  f"did not extend past pre-retest {cont['pre_retest_extreme']})")
        return out
    entry = round(e2.level, 2)
    stop = round(retest["price"] + buffer, 2) if short else round(retest["price"] - buffer, 2)
    out.update({"completed": True, "entry": entry, "stop": stop,
                "thesis": "1m break→retest→second-break of a NEW post-retest swing "
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
def grade(loc, sweep_side_ok, trigger_ok, room_R, context_clear=True, location_quality_ok=True):
    """Grade the EXECUTABLE setup (spec §7). A/A+/B require a COMPLETED in-direction trigger. A ALSO
    requires CLEAR HTF context (30m aligned) AND a quality (structure-breaking) 5m location AND >=2R
    credible room before the first meaningful obstacle. Mixed/unknown/counter-trend context or a mere
    candidate location cannot be A — it is capped at B (watch-only this phase). `room_R` is R to the FIRST
    meaningful obstacle, not the far target."""
    if not loc.get("nearest"):
        return {"grade": None, "trigger_completed": False, "why": "no preplanned location"}
    if not trigger_ok:
        return {"grade": None, "trigger_completed": False, "why": "location present, trigger pending (WATCH)"}
    if room_R is not None and room_R < MIN_R_B:
        return {"grade": None, "trigger_completed": True, "why": f"first-obstacle room {room_R}R < {MIN_R_B}"}
    a_ok = context_clear and location_quality_ok            # A needs clear context + quality location
    if a_ok and sweep_side_ok and room_R and room_R >= cfg("MIN_R_APLUS") and loc.get("confluence", 0) >= 1:
        g = "A+"
    elif a_ok and room_R and room_R >= MIN_R_A:
        g = "A"
    elif room_R and room_R >= MIN_R_B:
        g = "B"                                              # deficiency (context/quality/room) -> watch-only
    else:
        g = None
    why = "location+trigger+context+quality+room" if a_ok else "capped at B: context not clear or candidate location"
    return {"grade": g, "trigger_completed": True, "why": why}


# ── sub-model: obstacle scan on the reward path (defined, causal, no look-ahead) ──
ACCEPT_CLOSES = cfg("ACCEPT_CLOSES")   # PROVISIONAL (see CONFIG): consecutive closes to heuristically clear a level.


def _acceptance(bars, idx, level, below):
    """CAUSAL status of a level formed at bar `idx`, using ONLY bars idx+1..end (all <= the snapshot —
    never a candle after 'now'). This defines what invalidates an obstacle, per the review:

      CLEARED   — >= ACCEPT_CLOSES CONSECUTIVE completed candles CLOSED beyond `level` (real body
                  acceptance). A single close, a wick, or an indicator 'mitigated' flag is NOT enough.
                  (ACCEPT_CLOSES is PROVISIONAL — see CONFIG; acceptance is a heuristic, not the sole word.)
      WEAKENED  — a wick pierced the level but it HELD on a close basis. Still a live obstacle.
      ACTIVE    — price never traded beyond it since formation.
      UNKNOWN   — the level formed at the edge of the window with no completed bars after it, so clearance
                  CANNOT be verified; treated as still-obstructing (room is NOT awarded).

    Returns (status, evidence_text, ev_from_time, ev_to_time). `below=True` for a support (short path)."""
    tail = bars[idx + 1:]
    if not tail:
        return ("UNKNOWN", "no completed bars after formation — clearance unverifiable", None, None)
    run = 0
    run_from = None
    accepted = None
    wicked = False
    wick_t = None
    for b in tail:
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
        return ("CLEARED", f"accepted through: >= {ACCEPT_CLOSES} consecutive closes beyond {round(level, 2)} (provisional rule)",
                accepted[0], accepted[1])
    if wicked:
        return ("WEAKENED", "wick pierced but held on a close basis", wick_t, wick_t)
    return ("ACTIVE", "untested since formation", None, None)


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


def obstacle_scan(series_by_tf, direction, entry, stop_pts, threshold_level=None):
    """Scan the reward path from the proposed entry outward and list EVERY opposing structure in order of
    encounter (nearest first), across 15m/5m/1m: OB boundaries, swing pivots, and LRLR/HRLR pools. Each row
    carries price, timeframe, why it opposes, a STATUS (ACTIVE/WEAKENED/CLEARED/UNKNOWN, or TRIGGER_THRESHOLD
    for the level the trigger consumed) with completed-candle evidence + timestamp, distance/R, and whether
    it is a MEANINGFUL hard barrier (a bare 1m swing is minor).

    The gating `first` is the first still-obstructing MEANINGFUL level (ACTIVE/WEAKENED/UNKNOWN — a CLEARED
    level is excluded only with timestamped acceptance evidence; a TRIGGER_THRESHOLD is not an obstacle).
    We never skip a nearer meaningful obstacle for a better R, and never award room over an UNKNOWN level."""
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
        is_threshold = threshold_level is not None and abs(round(price, 2) - round(threshold_level, 2)) < TICK
        if is_threshold:
            status, ev = "TRIGGER_THRESHOLD", "the level the trigger consumed — not a profit-path obstacle"
        rows.append({"tf": tf, "price": round(price, 2), "zone": zone, "kind": kind, "opposes": opposes,
                     "status": status, "evidence": ev, "ev_from": evf, "ev_to": evt,
                     "meaningful": _meaningful_obstacle(kind, tf) and not is_threshold,
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

    # dedupe by rounded price; keep the strongest label (prefer still-obstructing, prefer OB/zone), merge TFs
    order = {"ACTIVE": 0, "WEAKENED": 1, "UNKNOWN": 2, "TRIGGER_THRESHOLD": 3, "CLEARED": 4}
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
    # gating obstacle = first STILL-OBSTRUCTING meaningful level (ACTIVE/WEAKENED/UNKNOWN). A CLEARED level
    # (timestamped acceptance) or a TRIGGER_THRESHOLD is not an obstacle; a minor 1m swing is context only.
    first = next((x for x in merged if x["status"] in ("ACTIVE", "WEAKENED", "UNKNOWN") and x["meaningful"]), None)
    return {"rows": merged, "first": first}


def _targets(series_by_tf, ctx, direction, entry):
    """Ordered price-dependent targets in the trade direction (spec §6): unmitigated liquidity pools from
    30m/15m/5m/1m plus EQ levels, nearest draw first. External 30m/15m BSL/SSL are included but are not the
    ONLY targets. Structural draws (opposing OB far edge, range edge) can extend this later."""
    if entry is None or not direction:
        return []
    short = direction == "SHORT"
    levels = set()
    for x in (ctx["external_ssl"] if short else ctx["external_bsl"]):
        levels.add(round(x, 2))
    for x in (ctx["eql"] if short else ctx["eqh"]):
        levels.add(round(x, 2))
    for tf in ("15m", "5m", "1m"):
        for r in LR.detect_liquidity_runs(series_by_tf.get(tf) or [], tick=0.25):
            if r.mitigated:
                continue
            if short and (not r.is_high) and r.price < entry:
                levels.add(round(r.price, 2))
            elif (not short) and r.is_high and r.price > entry:
                levels.add(round(r.price, 2))
    ordered = sorted([x for x in levels if (x < entry) == short], reverse=short)
    return ordered


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
    stop_floor = round((vol1 or vol5) * cfg("STOP_FLOOR_MULT"), 2)   # PROVISIONAL noise floor (see CONFIG)
    buf = max(2.0, round(vol1 * cfg("STOP_BUFFER_MULT"), 2)) if vol1 else max(2.0, round(vol5 * cfg("STOP_BUFFER_MULT"), 2))
    sw = (ev or {}).get("sweep")
    nd = loc.get("nearest")
    # DIRECTION comes from the LOCATION side (a valid location may be taken in either direction). The 30m
    # bias is a QUALITY factor, NOT a hard long/short permission (spec §3): alignment is required for A;
    # mixed/unknown or counter-trend context caps the grade below A.
    direction = ("LONG" if nd["dir"] == "demand" else "SHORT") if nd else None
    bias = ctx["bias30"]
    aligned = (bias == "bullish" and direction == "LONG") or (bias == "bearish" and direction == "SHORT")
    counter_trend = (bias == "bullish" and direction == "SHORT") or (bias == "bearish" and direction == "LONG")
    context_clear = bool(aligned)   # A needs clear HTF context; mixed/unknown/counter-trend does not qualify as A

    # ── LOCATION AUDIT — make the SELECTED 5m location fully evidenced (not "9 others exist") ──────
    loc_audit = None
    if nd:
        conf = loc.get("confluence", 0)
        # relationship to the 30m bearish/bullish context: does the 5m OB sit inside a 30m OB of the same side?
        htf_rel = "no 30m OB overlap"
        want_htf = "bear" if nd["dir"] == "supply" else "bull"
        for h in (ctx.get("htf_ob_30m") or []):
            if h["dir"] == want_htf and h["bottom"] <= nd["ref"] <= h["top"]:
                htf_rel = f"inside 30m {want_htf} OB [{_f(h['bottom'])}–{_f(h['top'])}]"
                break
        loc_audit = {
            "type": nd["type"], "dir": nd["dir"],
            "zone": f"{_f(nd.get('bottom'))}–{_f(nd.get('top'))}" if nd.get("top") is not None else _f(nd.get("ref")),
            "ref": nd.get("ref"), "status": nd.get("status"), "valid": nd.get("valid"),
            "confirmed_by": nd.get("confirmed_by"), "src_candle": nd.get("src_candle"), "left_time": nd.get("left_time"),
            "confluence": conf,
            "confirmed_confluence": conf >= 1,        # conf 0 = CANDIDATE, never counted as confirmed confluence
            "classification": "confirmed confluence" if conf >= 1 else "candidate (conf 0 — not confirmed confluence)",
            "aligned_with_30m": bool(aligned), "counter_trend": bool(counter_trend),
            "context_clear_for_A": context_clear, "htf_relationship": htf_rel,
            "quality": loc.get("quality"), "quality_reason": loc.get("quality_reason"),
            "alternatives": max(0, (loc.get("count") or 0) - 1),
        }

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
    targets_ordered = []
    if triggered and direction:
        entry = trg["entry"]; stop = trg["stop"]; stop_thesis = trg["thesis"]
        entry = _tick(entry, "near")
        # TARGETS from ordered price-dependent liquidity/structure (30m/15m/5m/1m pools + EQ), not only
        # external 30m/15m BSL/SSL (spec §6). Nearest draw first.
        targets_ordered = _targets(series_by_tf, ctx, direction, entry)
        tp1 = targets_ordered[0] if targets_ordered else None
        tp2 = targets_ordered[1] if len(targets_ordered) > 1 else None
        if direction == "SHORT":
            stop = _tick(stop, "up"); tp1 = _tick(tp1, "up"); tp2 = _tick(tp2, "up")
        else:
            stop = _tick(stop, "down"); tp1 = _tick(tp1, "down"); tp2 = _tick(tp2, "down")
        rs = risk_size(entry, stop, [tp1, tp2])
        best_R = max(rs.get("R_to_targets") or [0]) if rs.get("R_to_targets") else None
        sp = rs.get("stop_pts")
        degenerate_stop = bool(sp is not None and stop_floor and sp < stop_floor)
        scan = obstacle_scan(series_by_tf, direction, entry, sp,   # from the ACTUAL post-trigger entry
                             threshold_level=(threshold or {}).get("level"))
        fo = scan["first"]
        first_obst = fo["price"] if fo else None
        obstacle_R = fo["R"] if fo else None
        effective_R = obstacle_R if obstacle_R is not None else best_R

    # route that qualified the trigger — either a confirmed sweep+reclaim OR a structural rejection.
    route = ("sweep+reclaim" if (sw and sw.get("side") == ("BSL" if direction == "SHORT" else "SSL")
                                 and sw.get("mitigated"))
             else ("rejection+displacement (structural)" if triggered else None))

    loc_quality_ok = bool(nd and nd.get("meaningful"))       # structure-breaking OB / HRLR / LRLR, not a bare pivot
    gr = (grade(loc, route == "sweep+reclaim", True, effective_R,
                context_clear=context_clear, location_quality_ok=loc_quality_ok) if triggered
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
            stale = age > cfg("STALE_SECONDS")     # PROVISIONAL window (see CONFIG); should vary by session
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
    # invalidation already breached BEFORE entry: price has traded to/through the proposed stop -> the
    # short/long premise is void (price accepted beyond invalidation). Only meaningful post-trigger.
    inval_breached = bool(triggered and stop is not None and (
        (direction == "SHORT" and price >= stop) or (direction == "LONG" and price <= stop)))

    # ── GATES — reported SEPARATELY; ok=None means "n/a until the trigger" (never hides a real failure) ──
    thr = f"{_f(threshold['level'])}" if threshold else "—"
    la = loc_audit or {}
    gates = [
        ("stale data", not stale,
         (f"last bar {age_min} min old (run run-live.sh in market hours)" if stale else f"fresh ({age_min} min)")),
        ("valid 5m location in context direction", loc_ok,
         "no 5m location in the context direction" if not loc_ok else
         (f"{la.get('type')} {la.get('dir')} zone {la.get('zone')} · {la.get('classification')} · "
          f"{la.get('htf_relationship')}" + (f"; {alt_locs} other 5m location(s)" if alt_locs else ""))),
        ("location confidence (confirmed 5m confluence / timely trigger)", loc_conf_ok,
         (f"standalone conf {conf} — CANDIDATE, not confirmed confluence" + (f"; {alt_locs} other 5m location(s) exist" if alt_locs else "; no alternative 5m location")
          if not loc_conf_ok else ("confirmed confluence " + str(conf) if conf >= 1 else "resolved by timely in-direction trigger (OB stays a candidate)"))),
        ("1m not opposing the side", not conflict,
         "1m structure still opposes (developing reversal)" if conflict else "aligned"),
        ("trigger route qualified (sweep+reclaim OR rejection+displacement)", triggered,
         (f"{route}: break {trg['break']['level']} → retest {trg['retest']['level']} → second break {trg['second_break']['level']}"
          if triggered else
          f"PENDING — no completed break→retest→second-break yet; must break {thr} first" +
          (f"; candidate rejected: {trg.get('rejected_reason')}" if trg.get("rejected_reason") else "") +
          (f" (sweep {sw['price']} OPEN — does not by itself qualify or block)" if (sw and not sw.get('mitigated')) else ""))),
        ("invalidation not already breached", (None if not triggered else (not inval_breached)),
         ("deferred — set at trigger" if not triggered else
          (f"price {_f(price)} already beyond stop {_f(stop)} — premise void" if inval_breached else "intact"))),
        ("structural stop ≥ volatility floor", (None if not triggered else (not degenerate_stop)),
         ("deferred — set at trigger" if not triggered else (f"stop {sp}pt < 1m floor {stop_floor}pt" if degenerate_stop else f"{sp}pt ≥ {stop_floor}pt floor"))),
        ("permitted risk / sizeable stop", (None if not triggered else sizeable),
         ("deferred — set at trigger" if not triggered else (rs.get("reason") if not sizeable else f"{rs.get('contracts')} MNQ, ${rs.get('risk_per_account')}/acct"))),
        (f"≥ {MIN_R_A}R room before first meaningful obstacle", (None if not triggered else room_ok),
         ("deferred — measured from the post-trigger entry" if not triggered else
          (f"only {effective_R}R before {_f(first_obst)} ({fo['kind']} {fo['tf']})" if (fo and not room_ok)
           else (f"{effective_R}R clear to target" if room_ok else "room unverified")))),
        # EXECUTION-clearance gates: UNKNOWN in this environment -> a READY setup is NOT execution-cleared.
        ("news window verified (Asia/Jerusalem)", None,
         "UNVERIFIED — no live macro calendar in this environment; verify the no-entry window before any order"),
        ("account/copier/PDLL state known (both XFAs)", None,
         "UNKNOWN — live balances/DLL/PDLL/copier not available here; cannot clear execution or size vs remaining budget"),
    ]
    # a READY *setup* is never an execution clearance while account + news are unknown (spec §2/§8)
    execution_cleared = False

    # ── STATE MACHINE ─────────────────────────────────────────────────────────
    # WATCH is a PRE-trigger state only. Once the trigger completes the decision is binary: READY or
    # NO TRADE (a specific post-trigger gate failed — room / stop / risk / invalidation). Stale data and
    # a missing location are independent hard NO-TRADE reasons.
    pending = [f"{name}: {detail}" for name, ok, detail in gates if ok is False]
    reason_code = None
    if stale:
        verdict, state, reason_code = "🔴 No Trade", "NO_TRADE", "STALE_DATA"
        reason = next(f"{name} — {detail}" for name, ok, detail in gates if ok is False)
    elif not loc_ok:
        verdict, state, reason_code = "🔴 No Trade", "NO_TRADE", "NO_VALID_5M_LOCATION"
        reason = next(f"{name} — {detail}" for name, ok, detail in gates if ok is False)
    elif not triggered:
        verdict, state = "🟡 Watch", "WATCH"
        reason_code = "WATCH_TRIGGER"
        reason = (f"Armed — awaiting the trigger (sweep+reclaim OR rejection+displacement) at 5m "
                  f"{nd['type']} {nd['dir']} @ {_f(nd['ref'])}; price must break {thr} first. "
                  f"Entry, stop, size and R are computed at the trigger — not now.")
    elif degenerate_stop or (not sizeable) or inval_breached or (not room_ok):
        verdict, state = "🔴 No Trade", "NO_TRADE"     # trigger completed but a post-trigger gate failed
        reason_code = ("INVALIDATION_BREACHED" if inval_breached else "DEGENERATE_STOP" if degenerate_stop
                       else "RISK_NOT_PERMITTED" if not sizeable else "INADEQUATE_ROOM")
        reason = next(f"{name} — {detail}" for name, ok, detail in gates
                      if ok is False and name not in ("stale data", "valid 5m location in context direction"))
    elif gr.get("grade") not in ("A", "A+"):
        # all mechanical gates pass, but only A/A+ are eligible this phase — a B setup is WATCH-ONLY
        verdict, state, reason_code = "🟡 Watch", "WATCH", "B_WATCH_ONLY"
        reason = (f"Grade {gr.get('grade') or '—'} — watch-only this phase ({gr.get('why')}). "
                  f"Only A/A+ are eligible; not an order.")
    else:
        verdict, state, reason_code = "🟢 Ready", "READY", "READY_ORDER"
        reason = (f"Setup READY (route: {route}); grade {gr.get('grade')}. "
                  f"EXECUTION NOT CLEARED — verify news window + both accounts' remaining budget first.")

    # detailed state per spec §5 (the UI may keep simpler labels; this exposes the full lifecycle position)
    if state == "NO_TRADE":
        detailed_state = "NO_TRADE" if reason_code in ("STALE_DATA", "NO_VALID_5M_LOCATION") else "NO_TRADE_POST_TRIGGER"
    elif state == "WATCH":
        detailed_state = "WATCH_B_SETUP" if reason_code == "B_WATCH_ONLY" else ("WATCH_TRIGGER" if loc_ok else "WATCH_LOCATION")
    else:
        detailed_state = "READY_ORDER"
    # entry-type label: a completion/confirmation entry vs a prospective retest limit (spec §5)
    entry_type = None
    if triggered and entry is not None:
        entry_type = {"kind": "prospective_limit_retest",
                      "note": "limit at the broken 1m level; requires a FURTHER retest and may never fill — "
                              "WAITING_FOR_FILL until filled; cancel on invalidation/too-far/TTL/session/news."}

    lines = _report_lines(verdict, state, reason, symbol, price, direction, gr, ctx, loc, ev, st, tr,
                          entry, stop, rs, tp1, tp2, best_R, effective_R, first_obst, stop_thesis, pending, fo,
                          gates, scan, threshold, trg, route, triggered, loc_audit)
    return {"verdict": verdict, "state": state, "detailed_state": detailed_state, "reason_code": reason_code,
            "reason": reason, "symbol": symbol, "price": round(price, 2), "as_of": last_t,
            "direction": direction, "grade": gr.get("grade"), "context": ctx, "location": loc,
            "location_audit": loc_audit, "execution_cleared": execution_cleared, "entry_type": entry_type,
            "counter_trend": counter_trend, "context_clear": context_clear,
            "liquidity_event": ev, "structure": st, "trigger": tr, "risk": rs, "conflict": conflict,
            "triggered": triggered, "trigger_sequence": trg, "trigger_threshold": threshold, "route": route,
            "targets_ordered": targets_ordered,
            "pending": pending, "stop_thesis": stop_thesis, "degenerate_stop": degenerate_stop,
            "stop_floor": stop_floor, "vol_unit": vol5,
            "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "best_R": best_R,
            "first_obstacle": first_obst, "first_obstacle_detail": fo,
            "obstacle_R": obstacle_R, "effective_R": effective_R, "obstacle_scan": scan["rows"],
            "gates": [{"name": n, "ok": ok, "detail": d} for n, ok, d in gates],
            "stale": stale, "age_min": age_min, "flags": pending, "config_version": CONFIG_VERSION,
            "conditional": state != "READY", "lines": lines,
            "disclaimer": "Decision-support tool only — not advice, not a forecast, not a profit guarantee. "
                          "READY = setup-ready; it is NOT an execution clearance (news + account state unverified)."}


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
                  gates=None, scan=None, threshold=None, trg=None, route=None, triggered=False, loc_audit=None):
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
    # SELECTED 5m location — auditable evidence (source candle, status, 30m relationship, candidate flag)
    if loc_audit:
        la = loc_audit
        sc = la.get("src_candle")
        srcstr = "" if not sc else f" · source candle {_evt(sc.get('t'))} O{_f(sc['o'])} H{_f(sc['h'])} L{_f(sc['l'])} C{_f(sc['c'])}"
        lines.append(f"Selected 5m location: {la['type']} {la['dir']} zone {la['zone']} · {la['classification']}"
                     f" · {'aligned' if la['aligned_with_30m'] else 'NOT aligned'} with 30m {ctx['bias30']} · {la['htf_relationship']}"
                     f"{srcstr}")
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
            mark = {"ACTIVE": "●", "WEAKENED": "◐", "UNKNOWN": "◍", "TRIGGER_THRESHOLD": "△", "CLEARED": "○"}
            for r in rows[:8]:
                m = mark.get(r["status"], "?")
                z = f" [{r['zone']}]" if r.get("zone") else ""
                tag = ""
                if r["status"] == "CLEARED":
                    tag = "  ← EXCLUDED (accepted through)"
                elif r["status"] == "TRIGGER_THRESHOLD":
                    tag = "  ← trigger threshold (not an obstacle)"
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
    # EXECUTION + MANAGEMENT notes (spec §2/§8): this app does NOT monitor live positions or clear execution.
    lines.append("Execution NOT cleared here: verify the news window (Asia/Jerusalem) and BOTH accounts' "
                 "remaining PDLL budget before any order — those inputs are unavailable to this tool.")
    lines.append("Management (human plan, not automated): no BE before 1R; BE at 1R only after opposing "
                 "liquidity is taken or new structure+retest; trail only behind a CONFIRMED swing after 1.5R; "
                 "never widen a stop, add to a loser, or chase $150.")
    lines.append("Tool only — not advice, not a forecast, not a profit guarantee.")
    return lines
