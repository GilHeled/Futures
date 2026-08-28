"""Course PD ARRAYS — the imbalance objects and their CONTEXTUAL ROLE, plus the NWOG (Lesson 13) and
ORG (Lesson 14) context detectors v1 never implemented.

CORE PRINCIPLE (Lessons 10–12): a PD array (FVG / NWOG / ORG) is ONE object with TWO independent
attributes —
  • LIFECYCLE (`status`: unfilled → touched → mitigated) = what PRICE HAS DONE to the array;
  • ROLE     (`role`:  draw | reaction | entry | inactive) = what the array MEANS in the current trade
             context, assigned by `role_of` from TIMEFRAME + DEALING-RANGE POSITION + DIRECTION.
Role is NOT read off `status`: an *unfilled* 1m FVG in the correct retracement area is already the
planned ENTRY zone (Lesson 12), before price touches it; a *touched* HTF FVG can still be a reaction
area. `status` only tells us where the array is in its lifecycle, and drops a spent FVG out.

NWOG/ORG are **support/resistance + magnet/target** arrays — the course is explicit that they are NOT
liquidity/ERL (Lesson 13). Pure, causal functions over bars; v1 is imported nowhere and untouched.
Every parameter comes from the course text — no invented tolerances (per the project rule). The
rebalance-% statistics the lessons quote (≤60pt gaps ~always fill 50%, >200pt won't) are OBSERVATIONS,
not mechanical rules, so they are not encoded as gates — only surfaced context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time, timezone
from zoneinfo import ZoneInfo


# ---- PD ARRAY object + contextual role (Lessons 10–12) ----------------------------------------
PD_ROLES = ("draw", "reaction", "entry", "inactive")
#   draw     — an objective price is being pulled TOWARD (a liquidity magnet / strong reaction area)
#   reaction — a support/resistance level price interacts with (contextual confluence; QUALITY only)
#   entry    — an execution/entry-exit level (Lesson 12: FVGs are entry tools ONLY on 5m/1m)
#   inactive — spent / not relevant to the current direction (e.g. a mitigated FVG)

# Timeframe classes (Lesson 12 pins the entry scope to 5m/1m absolutely; the higher split follows the
# course hierarchy HTF = objective / strong reaction, MTF = context / support-resistance). Named, not
# magic — reviewable. Mirrors pipeline.tf_minutes (kept local to avoid a pdarrays→pipeline import).
_LTF_MAX_MIN = 5        # ≤5m  → LTF (entry-eligible; Lesson 12 "on 5m/1m only, as part of entry/exit")
_HTF_MIN_MIN = 240      # ≥4H  → HTF (draw-eligible; Lesson 11/16 "price draws toward a higher-interval FVG")


def _tf_min(tf: str) -> int:
    m = re.match(r"^\s*(\d*)\s*([mMhHdDwW])\s*$", tf or "")
    return int(m.group(1) or 1) * {"m": 1, "h": 60, "d": 1440, "w": 10080}[m.group(2).lower()] if m else 0


def tf_class(tf: str) -> str:
    """LTF (≤5m) | MTF (>5m, <4H) | HTF (≥4H) | 'unknown'. Drives the Lesson-12 role scoping."""
    mins = _tf_min(tf)
    if mins <= 0:
        return "unknown"
    if mins <= _LTF_MAX_MIN:
        return "LTF"
    if mins >= _HTF_MIN_MIN:
        return "HTF"
    return "MTF"


@dataclass
class PDArray:
    """A role-neutral PD-array / imbalance object (FVG / NWOG / ORG). LIFECYCLE and ROLE are distinct
    (see module docstring): `status` is what price has done to it; `role` is what it means now."""
    kind: str                       # "fvg" | "nwog" | "org"
    tf: str                         # timeframe the array lives on
    polarity: str                   # "bullish" | "bearish" ("" for the non-directional NWOG/ORG)
    top: float
    bottom: float
    ce: float                       # 50% (consequent encroachment) — the array's reference line
    status: str = "unfilled"        # LIFECYCLE: unfilled | touched | mitigated  (what price DID)
    source: object = None           # underlying v1 FVG / nwog|org dict (audit)
    role: str = ""                  # ROLE: assigned by role_of() — draw | reaction | entry | inactive
    role_basis: dict = field(default_factory=dict)   # WHY (tf_class, zone, side, polarity, lifecycle)

    def to_dict(self) -> dict:
        def px(x):
            return None if x is None else round(float(x), 2)
        return {"kind": self.kind, "tf": self.tf, "polarity": self.polarity,
                "top": px(self.top), "bottom": px(self.bottom), "ce": px(self.ce),
                "status": self.status, "role": self.role, "role_basis": dict(self.role_basis)}


def from_fvg(fvg, tf: str | None = None) -> PDArray:
    """Adapt a v1 FVG (frozen detector) into the role-neutral PDArray. v1 stays the detector."""
    return PDArray(kind="fvg", tf=(tf or getattr(fvg, "tf", "") or ""), polarity=fvg.direction,
                   top=float(fvg.top), bottom=float(fvg.bottom), ce=float(fvg.ce),
                   status=getattr(fvg, "status", "unfilled"), source=fvg)


def _from_gap(kind: str, d: dict, tf: str) -> PDArray:
    # NWOG/ORG are non-directional S/R+magnet arrays with a `closed` flag (rebalanced). They carry no
    # 'touched' state — coarser lifecycle: closed → mitigated (rebalanced), else unfilled.
    return PDArray(kind=kind, tf=tf, polarity="", top=float(d["top"]), bottom=float(d["bottom"]),
                   ce=float(d["mid"]), status=("mitigated" if d.get("closed") else "unfilled"), source=d)


def from_nwog(d: dict, tf: str = "W") -> PDArray:
    return _from_gap("nwog", d, tf)


def from_org(d: dict, tf: str = "D") -> PDArray:
    return _from_gap("org", d, tf)


def _side(zone: str, direction: str) -> str:
    """Which side of the dealing range the array sits on, relative to the TRADE direction (Lesson 12:
    the entry FVG is marked in the DISCOUNT for a long / PREMIUM for a short — the retracement side;
    the DRAW is the opposite side, toward the opposing liquidity). Returns 'retrace' | 'draw' | 'neutral'."""
    if zone in (None, "", "equilibrium"):
        return "neutral"
    if direction == "long":
        return "retrace" if zone == "discount" else "draw"
    if direction == "short":
        return "retrace" if zone == "premium" else "draw"
    return "neutral"


def _seeking_vs_reacting(status: str) -> str:
    """A distinct AUDIT dimension (NOT the role): has price INTERACTED with the array yet? Lesson 10 —
    price SEEKS an imbalance it has not reached; it REACTS at one it has already touched. Derived from
    lifecycle (this is legitimately what seeking/reacting means), kept SEPARATE from the role, which is
    driven by timeframe + position."""
    if status == "mitigated":
        return "spent"
    return "reacting" if status == "touched" else "seeking"


def role_of(array: PDArray, *, direction: str, zone: str | None, erl_irl: str | None = None) -> PDArray:
    """Assign the array's CONTEXTUAL ROLE and record a FULL, AUDITABLE decision trace, then return the
    same array (mutated). The role is NOT hidden in code: every dimension that informs it — and a
    human-readable `rule` — lands in `array.role_basis` so each decision can be checked against the
    course from the dashboard / snapshot JSON.

    ROLE ≠ LIFECYCLE. The role is driven by TIMEFRAME class + DEALING-RANGE POSITION (`zone`, from
    HTFContext.zone of the array's CE) + trade DIRECTION — never read off `status`:
      • LTF (≤5m) on the retracement side  → 'entry'   (Lesson 12: 5m/1m FVGs are entry/exit tools;
                                                         an UNFILLED one in the zone is the *planned*
                                                         entry — lifecycle, surfaced separately, tells
                                                         whether it is armed vs already live);
      • HTF (≥4H) on the draw side         → 'draw'    (Lesson 11/16: price draws toward a
                                                         higher-interval FVG — an objective);
      • otherwise                          → 'reaction' (contextual support/resistance; QUALITY only).
    `status` only removes a SPENT array: a mitigated FVG → 'inactive'; a closed NWOG/ORG stays a
    'reaction' (Lesson 13: keep the marking even after the gap closes). Polarity and liquidity class
    (ERL/IRL) are recorded for transparency; polarity does NOT gate the role — a polarity rule is a
    deferred, reviewable [RES], not invented now."""
    tc = tf_class(array.tf)
    side = _side(zone, direction)
    ctx = _seeking_vs_reacting(array.status)
    # --- decide the role, and state the RULE that produced it (auditable, not hidden) ---
    if array.status == "mitigated":
        role = "reaction" if array.kind in ("nwog", "org") else "inactive"
        rule = ("closed gap kept as support/resistance (Lesson 13: keep the marking after it closes)"
                if role == "reaction" else "mitigated FVG — spent, not relevant to this trade")
    elif side == "retrace":
        role = "entry" if tc == "LTF" else "reaction"
        rule = (f"{tc} FVG on the retracement side → ENTRY (Lesson 12: FVG is an entry tool on 5m/1m)"
                if role == "entry" else
                f"{tc} array on the retracement side → REACTION / support-resistance (entry is LTF-only, Lesson 12)")
    elif side == "draw":
        role = "draw" if tc == "HTF" else "reaction"
        rule = (f"{tc} array on the draw side → DRAW / objective (Lesson 11/16: price seeks a higher-TF FVG)"
                if role == "draw" else
                f"{tc} array on the draw side → REACTION (a draw objective is HTF-only)")
    else:
        role = "reaction"
        rule = "no clear dealing-range side (equilibrium / no range) → REACTION"
    array.role = role
    array.role_basis = {
        "tf_class": tc,                       # HTF / MTF / LTF          (Lesson 12 scope)
        "dealing_range_position": zone,       # premium / discount / equilibrium
        "liquidity_class": erl_irl,           # ERL / IRL                (external vs internal — Lesson 10)
        "seeking_vs_reacting": ctx,           # seeking / reacting / spent
        "side": side,                         # draw / retrace / neutral (the decision axis)
        "polarity": array.polarity,           # bullish / bearish        (surfaced, NOT gating)
        "lifecycle": array.status,            # unfilled / touched / mitigated
        "rule": rule,                         # human WHY for the final role
        "role": role,                         # final role (mirrors array.role)
    }
    return array

_ET = ZoneInfo("America/New_York")            # ET, DST-correct — no hard-coded Israeli clock (§11)
_RTH_OPEN = time(9, 30)                        # ORG new-day open = 09:30 ET (lesson: 16:30 Israel)
_PREV_CLOSE = time(16, 15)                     # ORG prior-day close = 16:15 ET (lesson: 23:15 Israel)


def _et(dt):
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(_ET)


# A new trading week = the first bar after the weekend break. CME's weekend (~49h, Fri 17:00 →
# Sun 18:00 ET) dwarfs the ~1h daily maintenance break, so a >24h gap between consecutive bars is an
# unambiguous, timezone-agnostic weekly-open marker (the lesson's "Friday close → new-week open").
_WEEKEND_GAP_HOURS = 24


def nwogs(bars, keep: int = 3) -> list:
    """New Week Opening Gaps (Lesson 13): the gap between the last price before the weekend and the
    first price of the new trading week. Support/resistance + a magnet/target; NOT liquidity/ERL.

    Course rules (Lesson 13): mark **three**; keep the marking even after a gap closes; additionally
    keep an **unclosed** NWOG older than **three weeks**. 'Closed' = price rebalanced the gap: a later
    completed bar's body CLOSES back through the pre-weekend price (Friday close) — the course's
    body-close convention (Lesson 12). Causal / no-repaint.

    Returns the tracked NWOGs oldest→newest: {top, bottom, mid, opened, closed, age_weeks}."""
    if not bars or len(bars) < 2:
        return []
    last_t = bars[-1].open_time
    tracked = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if (cur.open_time - prev.close_time).total_seconds() / 3600.0 < _WEEKEND_GAP_HOURS:
            continue                                       # not the weekly reopen
        friday_close, week_open = float(prev.close), float(cur.open)
        if friday_close == week_open:
            continue                                       # no gap
        top, bottom = max(friday_close, week_open), min(friday_close, week_open)
        gap_up = week_open > friday_close                  # opened above Friday close → fill = trade back down
        # closed when a LATER bar closes back through the Friday-close edge (the gap is rebalanced)
        closed = any((b.close <= friday_close) if gap_up else (b.close >= friday_close)
                     for b in bars[i:])
        age_weeks = (last_t - cur.open_time).total_seconds() / (7 * 86400.0)
        tracked.append({"top": round(top, 2), "bottom": round(bottom, 2),
                        "mid": round((top + bottom) / 2.0, 2),
                        "opened": cur.open_time.isoformat(), "closed": closed,
                        "age_weeks": round(age_weeks, 1)})
    if len(tracked) <= keep:
        return tracked
    recent = tracked[-keep:]                               # the course's three most recent
    older_unclosed = [t for t in tracked[:-keep]           # + retain an unclosed gap older than 3 weeks
                      if not t["closed"] and t["age_weeks"] > 3]
    return older_unclosed + recent


def org(bars):
    """Opening Range Gap (Lesson 14): the gap between the prior day's close (16:15 ET) and the current
    day's open (09:30 ET RTH open). Its KEY level is the 50% midpoint (the course marks that line);
    S/R + magnet/target that identifies the day's opening potential. The course tracks ONLY the
    CURRENT day's ORG. Needs intraday bars (≤15m) whose ET timestamps include 09:30 and 16:15.
    'Closed' = a later bar's body closes back through the prior-day-close edge (rebalanced). The
    "~70% close at least half" figure is an OBSERVATION and is not encoded as a rule. Causal.
    Returns {top, bottom, mid, closed} for today's ORG, or None if it can't be formed yet."""
    if not bars:
        return None
    today = _et(bars[-1].open_time).date()
    open_bar = next((b for b in bars                        # today's 09:30 ET open
                     if _et(b.open_time).date() == today and _et(b.open_time).time() >= _RTH_OPEN), None)
    if open_bar is None:
        return None
    today_open = float(open_bar.open)
    prior = [b for b in bars                                # prior day's close at/through 16:15 ET
             if _et(b.open_time).date() < today and _et(b.open_time).time() <= _PREV_CLOSE]
    if not prior:
        return None
    prev_close = float(prior[-1].close)
    if prev_close == today_open:
        return None                                         # no gap
    top, bottom = max(prev_close, today_open), min(prev_close, today_open)
    gap_up = today_open > prev_close
    open_t = open_bar.open_time
    closed = any((b.close <= prev_close) if gap_up else (b.close >= prev_close)
                 for b in bars if b.open_time >= open_t)     # rebalanced back to the prior-day close
    return {"top": round(top, 2), "bottom": round(bottom, 2),
            "mid": round((top + bottom) / 2.0, 2), "closed": closed}
