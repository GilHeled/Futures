"""Market-OUTCOME labelling — SEPARATE from feature generation, attached later by candidate id.

This pass is the ONLY place that looks at bars AFTER the decision. It is never imported into the
engine/feature path and its output must never re-enter the live market state (there are tests for
both). Outcomes answer "what happened in the market?", never "would the methodology take this?"
(that is the human-fidelity dataset), and are for the future OUTCOME model + analysis only — they
do NOT tune the deterministic strategy.

Design constraints (all enforced):
  * No intrabar hindsight: with OHLC-only bars, if stop and target are both touched on the SAME
    bar, the course outcome is `AMBIGUOUS_INTRABAR` — never the favorable interpretation.
  * MFE/MAE begin at the executable ENTRY (the fill), not pre-fill movement.
  * Explicit, bounded horizon: every label records when it became final (STOP / TARGET /
    AMBIGUOUS_INTRABAR / HORIZON / NO_FILL) — no setup runs forever.
  * Three outcome definitions kept SEPARATE (never a single `winner`):
      course_execution  — managed entry→stop/target under frozen assumptions;
      fixed_r           — did price reach +1R/+2R/+3R (before stop)?;
      liquidity_target  — was the intended opposing-liquidity objective reached?
Risk unit R = |entry - stop|.
"""
from __future__ import annotations

from typing import Optional

from ict_live.market.bar import Bar
from ict_live.market.calendar import Calendar

DEFAULT_HORIZON_BARS = 96          # explicit research horizon (~1 session of 15m); caller may override


def label_setup(*, id: str, symbol: Optional[str], tf: str, direction: str, entry: float,
                stop: float, target: Optional[float], decision_index: int, bars: list[Bar],
                horizon_bars: int = DEFAULT_HORIZON_BARS, calendar: Optional[Calendar] = None) -> dict:
    short = direction == "short"
    risk = abs(entry - stop)
    base = {"id": id, "symbol": symbol, "tf": tf, "direction": direction,
            "entry": entry, "stop": stop, "target": target, "risk": round(risk, 4),
            "decision_index": decision_index, "horizon_bars": horizon_bars}
    if risk <= 0:
        return {**base, "status": "invalid_risk"}

    # --- fill: first bar at/after the decision whose range contains the entry (executable) ---
    fill = None
    fill_scan_end = min(len(bars), decision_index + horizon_bars + 1)
    for j in range(decision_index, fill_scan_end):
        if bars[j].low <= entry <= bars[j].high:
            fill = j
            break
    if fill is None:
        return {**base, "status": "labelled", "fill_index": None,
                "course_execution": {"result": "NO_FILL"},
                "fixed_r": {}, "liquidity_target": {}, "excursion": {}, "session_end": {}}

    end = min(len(bars) - 1, fill + horizon_bars)

    def stop_hit(b): return b.high >= stop if short else b.low <= stop

    def target_hit(b): return (target is not None) and (b.low <= target if short else b.high >= target)

    def r_level(n): return entry - n * risk if short else entry + n * risk

    def r_reached(b, n):
        lvl = r_level(n)
        return b.low <= lvl if short else b.high >= lvl

    reward_R = round(abs(entry - target) / risk, 3) if target is not None else None

    # --- course execution (ordered scan; intrabar ambiguity is explicit) ---
    result, final, realized = None, None, None
    for j in range(fill, end + 1):
        b = bars[j]
        s, t = stop_hit(b), target_hit(b)
        if s and t:
            result, final, realized = "AMBIGUOUS_INTRABAR", j, None
            break
        if t:
            result, final, realized = "TARGET", j, reward_R
            break
        if s:
            result, final, realized = "STOP", j, -1.0
            break
    if result is None:
        result, final = "HORIZON", end
        mc = bars[end].close
        realized = round(((entry - mc) if short else (mc - entry)) / risk, 3)
    course = {"result": result, "final_index": final,
              "final_time": bars[final].open_time.isoformat(),
              "bars_to_final": final - fill, "realized_R": realized}

    # first stop bar (shared by fixed_r and liquidity_target ordering)
    stop_bar = next((j for j in range(fill, end + 1) if stop_hit(bars[j])), None)

    # --- fixed R reach (independent of the liquidity target) ---
    fixed = {}
    for n in (1, 2, 3):
        rn_bar = next((j for j in range(fill, end + 1) if r_reached(bars[j], n)), None)
        if rn_bar is None:
            hit, bars_to = False, None
        elif stop_bar is not None and stop_bar < rn_bar:
            hit, bars_to = False, None
        elif stop_bar is not None and stop_bar == rn_bar:
            hit, bars_to = "ambiguous", rn_bar - fill
        else:
            hit, bars_to = True, rn_bar - fill
        fixed[f"r{n}_hit"] = hit
        fixed[f"bars_to_r{n}"] = bars_to

    # --- liquidity target ---
    tgt_bar = next((j for j in range(fill, end + 1) if target_hit(bars[j])), None)
    if tgt_bar is None:
        before_stop = False
    elif stop_bar is None or tgt_bar < stop_bar:
        before_stop = True
    elif tgt_bar == stop_bar:
        before_stop = "ambiguous"
    else:
        before_stop = False
    liq = {"target_price": target, "reached": tgt_bar is not None,
           "reached_before_stop": before_stop,
           "bars_to_target": (tgt_bar - fill) if tgt_bar is not None else None}

    # --- excursion (from the fill; in R) over fill..final ---
    lo = min(b.low for b in bars[fill:final + 1])
    hi = max(b.high for b in bars[fill:final + 1])
    mfe = round(((entry - lo) if short else (hi - entry)) / risk, 3)
    mae = round(((hi - entry) if short else (entry - lo)) / risk, 3)
    excursion = {"mfe_R": mfe, "mae_R": mae}

    # --- session-end mark (independent metric) ---
    session_end = {}
    cal = calendar or Calendar()
    fill_day = cal.session_day(bars[fill].open_time)
    if fill_day is not None:
        se_index = fill
        for j in range(fill, end + 1):
            if cal.session_day(bars[j].open_time) == fill_day:
                se_index = j
            else:
                break
        se_close = bars[se_index].close
        session_end = {"index": se_index, "bars_to": se_index - fill,
                       "mark_R": round(((entry - se_close) if short else (se_close - entry)) / risk, 3)}

    return {**base, "status": "labelled", "fill_index": fill,
            "fill_time": bars[fill].open_time.isoformat(),
            "course_execution": course, "fixed_r": fixed, "liquidity_target": liq,
            "excursion": excursion, "session_end": session_end}


# feature-record keys the outcome payload must never collide with (leakage guard; see tests)
OUTCOME_TOP_KEYS = frozenset({"course_execution", "fixed_r", "liquidity_target", "excursion",
                              "session_end", "fill_index", "fill_time", "realized_R"})
