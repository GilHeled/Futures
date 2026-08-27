"""Course PD-array CONTEXT detectors that v1 never implemented: NWOG (Lesson 13) and ORG (Lesson 14).

These are **support/resistance + magnet/target** arrays — the course is explicit that they are NOT
liquidity/ERL (Lesson 13). Pure, causal functions over bars; v1 is imported nowhere and untouched.
Every parameter comes from the course text — no invented tolerances (per the project rule). The
rebalance-% statistics the lessons quote (≤60pt gaps ~always fill 50%, >200pt won't) are OBSERVATIONS,
not mechanical rules, so they are not encoded as gates — only surfaced context.
"""
from __future__ import annotations

from datetime import time, timezone
from zoneinfo import ZoneInfo

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
