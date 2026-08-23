"""visual_audit — the dev-only fidelity microscope (Phase 3).

Puts TradingView Bar Replay at a causal point, runs the SAME engine code on the SAME
(replay-truncated, Phase-0-proven-causal) bars, overlays the engine's interpretation, screenshots
it, and writes a report. Lets us compare, bar-for-bar, what the engine "sees" vs the course chart.

IMPORTANT — data authority: for a *historical* audit the bars are sourced from TradingView's
replay OHLCV. That is a DEV-ONLY, NON-AUTHORITATIVE input used purely to drive the microscope; it
never enters the production webhook->raw-1m pipeline, which remains the sole source of truth. The
engine core is agnostic to where Bars come from; only this dev harness chooses to read them from
TV. If a local raw store for the date exists, the audit cross-checks and REPORTS any mismatch —
never silently reconciles (per the frozen isolation rules).

Scope today: candidate SWINGS on the analysis TF — exactly what we need to settle `sig_swing`
visually. The overlay framework already covers all 15 primitives; the rest render automatically
as the engine starts producing them (dealing range / sweep / MSS / FVG / entry-stop-target are
still gated on the `sig_swing` freeze and later increments, and the report says so).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ict_live.devtools.tvmcp import overlay
from ict_live.devtools.tvmcp.client import TvClient
from ict_live.devtools.tvmcp.probe import latest_ts, to_epoch
from ict_live.engine import pipeline
from ict_live.market.bar import Bar
from ict_live.market.calendar import ET
from ict_live.structure import ids, swing_liquidity

RESULTS = Path(__file__).with_name("results")

# TV chart resolution -> (our TF code, minutes per bar)
_RES = {"1": ("1m", 1), "5": ("5m", 5), "15": ("15m", 15), "60": ("1H", 60),
        "240": ("4H", 240), "D": ("D", 1440), "W": ("W", 10080)}

# which of the 15 primitives the engine produces TODAY vs why not (for the report)
_PENDING = {
    "erl_pool": "needs D/session bars (not sourced in audit v1)",
    "irl_pool": "IRL/FVG layer not built",
    "dealing_range": "gated on sig_swing (B2)",
    "premium": "gated on sig_swing (dealing range)",
    "discount": "gated on sig_swing (dealing range)",
    "equilibrium": "gated on sig_swing (dealing range)",
    "sweep": "setup/state-machine increment",
    "manip_extreme": "setup/state-machine increment",
    "displacement": "setup/state-machine increment (B5)",
    "mss": "gated on sig_swing (B6)",
    "mss_close": "gated on sig_swing (B6)",
    "fvg": "IRL/FVG layer not built",
    "entry": "setup engine (B8/B9)",
    "stop": "setup engine (geometry invariants)",
    "target": "setup engine (B4)",
    "target_liq": "setup engine (B4)",
    "amd": "AMD phase track (B7)",
    "htf": "HTF context (B10)",
    "rejection": "setup engine (rejection reasons)",
}


def tv_ohlcv_to_bars(rows: list[dict], tv_res: str) -> tuple[list[Bar], str]:
    our_tf, minutes = _RES.get(str(tv_res), ("1H", 60))
    out: list[Bar] = []
    seen = set()
    for r in sorted(rows, key=lambda x: int(x["time"])):
        t = int(r["time"])
        if t in seen:
            continue
        seen.add(t)
        ot = datetime.fromtimestamp(t, tz=ET)
        try:
            out.append(Bar(our_tf, ot, ot + timedelta(minutes=minutes),
                           float(r["open"]), float(r["high"]), float(r["low"]),
                           float(r["close"]), float(r.get("volume", 0.0))))
        except (ValueError, KeyError, TypeError):
            continue     # skip a malformed row; audit reports the drop count
    return out, our_tf


def _cursor(tv: TvClient, start_res, status_res) -> Optional[float]:
    c = latest_ts(status_res.data) if status_res.data is not None else None
    if c is None and isinstance(start_res.data, dict):
        cd = start_res.data.get("current_date")
        c = to_epoch(cd) if cd is not None else None
    return c


def _load_course(path: Optional[str]) -> list[dict]:
    """Course annotations: {"swings":[{kind,time(iso|unix),price,label?}]}. -> unix-time items."""
    if not path:
        return []
    doc = json.loads(Path(path).read_text())
    items = []
    for s in doc.get("swings", []):
        t = s["time"]
        if isinstance(t, str):
            t = int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp())
        items.append({"kind": s["kind"], "time": int(t), "price": float(s["price"]),
                      "label": s.get("label", "")})
    return items


def _compare_to_course(eng_swings, course: list[dict], tf_minutes: int) -> dict:
    """Objective match of engine swings vs course-labelled swings. Match = same kind, time
    within one bar, price within 0.1%. (Points at structural swings; the course remains the
    fidelity reference for which structural swings become liquidity draws.)"""
    eng = list(eng_swings)
    tol_t = tf_minutes * 60
    matched, misses = [], []
    used = set()
    for c in course:
        hit = None
        for i, s in enumerate(eng):
            if i in used or s.kind != c["kind"]:
                continue
            if abs(int(s.time.timestamp()) - c["time"]) <= tol_t and \
               abs(s.price - c["price"]) <= max(abs(c["price"]) * 0.001, 1e-9):
                hit = i
                break
        if hit is not None:
            used.add(hit)
            matched.append(c)
        else:
            misses.append(c)
    extras = [{"kind": s.kind, "time": s.time.isoformat(), "price": s.price}
              for i, s in enumerate(eng) if i not in used]
    return {"course_total": len(course), "matched": len(matched),
            "missed": [{"kind": m["kind"], "price": m["price"], "label": m.get("label", "")} for m in misses],
            "extras": extras}


def visual_audit(tv: TvClient, symbol: str, date: str, *, timeframe: str = "60",
                 steps: int = 0, out_name: Optional[str] = None,
                 annotations_path: Optional[str] = None, show_rejected: bool = True,
                 show_swept: bool = True) -> dict:
    tv.set_symbol(symbol)
    tv.set_timeframe(timeframe)
    start = tv.replay_start(date)
    for _ in range(max(0, steps)):
        tv.replay_step()
    status = tv.replay_status()
    cursor = _cursor(tv, start, status)

    oh = tv.ohlcv()
    rows = (oh.data or {}).get("bars") if isinstance(oh.data, dict) else None
    rows = rows or ((oh.data or {}).get("last_5_bars") if isinstance(oh.data, dict) else []) or []
    bars, our_tf = tv_ohlcv_to_bars(rows, timeframe)
    # defensive truncation at the cursor (Phase 0 proved TV already truncates; belt-and-suspenders)
    if cursor is not None:
        bars = [b for b in bars if b.open_time.timestamp() <= cursor + 1]

    # single shared engine (same pipeline as replay/live) — no detection logic in the microscope
    ms = pipeline.analyze(bars, our_tf)
    classified, structural, pools = ms.classified, ms.structural, ms.pools
    tier_counts, liq_counts, ranges = ms.tier_counts, ms.liq_counts, ms.ranges
    ranked_sweeps, ranked_disp = ms.ranked_sweeps, ms.ranked_displacements
    ranked_mss, ranked_fvg, ranked_setups = ms.ranked_mss, ms.ranked_fvgs, ms.ranked_setups
    recommendation = ms.recommendation

    course = _load_course(annotations_path)
    comparison = _compare_to_course(structural, course,
                                    _RES.get(str(timeframe), ("1H", 60))[1]) if course else None

    ref_time = int(bars[-1].open_time.timestamp()) if bars else int(datetime.now(ET).timestamp())
    annotations = (overlay.from_dealing_ranges(ranges)
                   + overlay.from_structural(classified, show_rejected=show_rejected)
                   + overlay.from_swing_liquidity(pools, show_swept=show_swept)
                   + overlay.from_ranked_sweeps(ranked_sweeps)
                   + overlay.from_ranked_displacements(ranked_disp, bars)
                   + overlay.from_ranked_mss(ranked_mss, bars)
                   + overlay.from_ranked_fvgs(ranked_fvg, bars)
                   + overlay.from_ranked_setups(ranked_setups, ref_time))
    if course:
        annotations += overlay.from_course(course)
    render = overlay.render(tv, annotations, clear=True)

    # decision log: every derived object carries its id, WHY, and depends_on (dependency chain)
    decisions = []
    for cs in classified:
        decisions.append({"id": ids.swing_id(cs.swing), "depends_on": [],
                          "object": f"{cs.tier} swing {cs.swing.kind} {cs.swing.price:g}",
                          "why": cs.reason})
    for p in pools:
        src_swing_id = f"SW{p.index}{'H' if p.kind == 'high' else 'L'}"   # ERL derives from its swing
        decisions.append({"id": ids.pool_id(p), "depends_on": [src_swing_id],
                          "object": f"ERL {'BSL' if p.kind == 'high' else 'SSL'} {p.price:g} "
                          f"({'swept' if p.swept else 'active'})", "why": p.reason})
    for dr in ranges:
        decisions.append({"id": ids.dr_id(dr), "depends_on": [],
                          "object": f"dealing range [{dr.source_tf}] {dr.low:g}–{dr.high:g} "
                          f"({dr.direction})", "why": dr.reason})
    for r in ranked_sweeps:
        sw = r.item
        facsum = "; ".join(f"{f.name}={f.value:g}" if isinstance(f.value, float)
                           else f"{f.name}={f.value}" for f in r.factors)
        decisions.append({"id": sw.id, "depends_on": list(sw.depends_on),
                          "object": f"manipulation #{r.rank} {sw.direction} @ {sw.pool_price:g} "
                          f"(ext {sw.extreme:g})", "why": f"{sw.reason} | rank factors: {facsum}"})
    for r in ranked_disp:
        d = r.item
        decisions.append({"id": d.id, "depends_on": list(d.depends_on),
                          "object": f"displacement #{r.rank} {d.direction} net={d.net:g}",
                          "why": d.reason})
    for r in ranked_mss:
        m = r.item
        decisions.append({"id": m.id, "depends_on": list(m.depends_on),
                          "object": f"MSS #{r.rank} {m.direction} {m.state} @ {m.broken_price:g}",
                          "why": m.reason})
    for r in ranked_fvg:
        f = r.item
        decisions.append({"id": f.id, "depends_on": list(f.depends_on),
                          "object": f"FVG #{r.rank} {f.direction} {f.status} CE {f.ce:g}",
                          "why": f.reason})
    for r in ranked_setups:
        s = r.item
        decisions.append({"id": s.id, "depends_on": list(s.depends_on),
                          "object": f"setup #{r.rank} {s.direction} "
                          f"{'ACTIONABLE' if s.actionable else 'rejected'} RR {s.rr}",
                          "why": s.reason})

    # frame the view: last ~120 bars up to the cursor + a small right margin
    _, minutes = _RES.get(str(timeframe), ("1H", 60))
    if cursor is not None:
        tv.set_visible_range(int(cursor - 120 * minutes * 60), int(cursor + 15 * minutes * 60))

    shot = tv.screenshot("chart") if out_name is None else tv.run("screenshot", "-r", "chart", "-o", out_name)
    shot_path = (shot.data or {}).get("file_path") if isinstance(shot.data, dict) else None
    tv.replay_stop()

    result = {
        "symbol": symbol, "date": date, "timeframe": timeframe, "our_tf": our_tf,
        "cursor": (datetime.fromtimestamp(cursor, tz=ET).isoformat() if cursor else None),
        "fractal_width": ms.fractal_width, "n_bars": len(bars), "n_rows_raw": len(rows),
        "tier_counts": tier_counts, "liq_counts": liq_counts,
        "active_erl": [{"kind": p.kind, "time": p.time.isoformat(), "price": p.price}
                       for p in swing_liquidity.active(pools)],
        "dealing_ranges": [{"source_tf": dr.source_tf, "low": dr.low, "high": dr.high,
                            "ce": dr.ce, "direction": dr.direction, "reason": dr.reason}
                           for dr in ranges],
        "ranked_sweeps": [{"rank": r.rank, "tied": r.tied, "id": r.item.id,
                           "direction": r.item.direction, "pool_price": r.item.pool_price,
                           "extreme": r.item.extreme,
                           "factor_order": [f.name for f in r.factors],
                           "factors": {f.name: f.value for f in r.factors},
                           "explanations": {f.name: f.explanation for f in r.factors},
                           "lost_to_prev": r.lost_to_prev, "why": r.item.reason,
                           "depends_on": list(r.item.depends_on)} for r in ranked_sweeps],
        "ranked_displacements": [{"rank": r.rank, "tied": r.tied, "id": r.item.id,
                                  "direction": r.item.direction, "net": r.item.net,
                                  "span": r.item.span, "exhausted": r.item.exhausted,
                                  "factor_order": [f.name for f in r.factors],
                                  "factors": {f.name: f.value for f in r.factors},
                                  "lost_to_prev": r.lost_to_prev, "why": r.item.reason,
                                  "depends_on": list(r.item.depends_on)} for r in ranked_disp],
        "ranked_mss": [{"rank": r.rank, "tied": r.tied, "id": r.item.id,
                        "direction": r.item.direction, "state": r.item.state,
                        "broken_price": r.item.broken_price,
                        "factor_order": [f.name for f in r.factors],
                        "factors": {f.name: f.value for f in r.factors},
                        "lost_to_prev": r.lost_to_prev, "why": r.item.reason,
                        "depends_on": list(r.item.depends_on)} for r in ranked_mss],
        "ranked_fvgs": [{"rank": r.rank, "tied": r.tied, "id": r.item.id,
                         "direction": r.item.direction, "status": r.item.status,
                         "ce": r.item.ce, "top": r.item.top, "bottom": r.item.bottom,
                         "factor_order": [f.name for f in r.factors],
                         "factors": {f.name: f.value for f in r.factors},
                         "lost_to_prev": r.lost_to_prev, "why": r.item.reason,
                         "depends_on": list(r.item.depends_on)} for r in ranked_fvg],
        "ranked_setups": [{"rank": r.rank, "tied": r.tied, "id": r.item.id,
                           "direction": r.item.direction, "entry": r.item.entry,
                           "stop": r.item.stop, "target": r.item.target, "rr": r.item.rr,
                           "actionable": r.item.actionable, "reject_reason": r.item.reject_reason,
                           "factor_order": [f.name for f in r.factors],
                           "factors": {f.name: f.value for f in r.factors},
                           "lost_to_prev": r.lost_to_prev, "why": r.item.reason,
                           "depends_on": list(r.item.depends_on)} for r in ranked_setups],
        "recommendation": {"decision": recommendation.decision, "reason": recommendation.reason,
                           "depends_on": list(recommendation.depends_on),
                           "setup_id": recommendation.setup.id if recommendation.setup else None},
        "decisions": decisions,
        "comparison": comparison,
        "render": render, "screenshot": shot_path,
        "data_source": "TradingView replay OHLCV (DEV-ONLY, non-authoritative)",
    }
    result["report"] = _write_report(result, annotations)
    return result


def _write_report(r: dict, annotations) -> str:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    counts = overlay.roles_present(annotations)
    L = [f"# Visual audit — {r['symbol']} @ {r['date']} ({r['timeframe']})", "",
         f"- causal replay cursor: **{r['cursor']}**",
         f"- analysis TF: {r['our_tf']}  ·  fractal width: {r['fractal_width']}",
         f"- bars fed to engine: {r['n_bars']} (raw rows {r['n_rows_raw']})",
         f"- data source: {r['data_source']}",
         f"- screenshot: `{r['screenshot']}`",
         f"- overlay: drew {r['render']['count']} shapes"
         + (f", {len(r['render']['errors'])} errors" if r['render']['errors'] else ""), ""]
    rec = r.get("recommendation")
    if rec:
        L += ["## RECOMMENDATION", "", f"# → {rec['decision']}", "", f"{rec['reason']}"]
        if rec.get("depends_on"):
            L += ["", f"**Traceable chain:** {' ← '.join(rec['depends_on'])}"]
        L += [""]
    tc = r["tier_counts"]
    lc = r["liq_counts"]
    L += ["## Objective structural swings (EXPERIMENTAL — not frozen)", "",
          f"- candidate (all fractal pivots): **{sum(tc.values())}**",
          f"- rejected (noise, collapsed): **{tc['rejected']}**",
          f"- structural (skeleton): **{tc['structural']}**", "",
          "## Liquidity relevance (context-dependent, decided here — NOT a swing attribute)", "",
          f"- active ERL (structural swings still unswept — potential draws): **{lc['active']}**",
          f"- swept ERL (liquidity already taken): **{lc['swept']}**", ""]
    if r["active_erl"]:
        L += ["| # | side | time | price |", "|---|---|---|---|"]
        for i, s in enumerate(r["active_erl"]):
            side = "BSL" if s["kind"] == "high" else "SSL"
            L.append(f"| {i} | {side} | {s['time']} | {s['price']:g} |")
    else:
        L.append("_no active ERL at this cursor_")
    drs = r.get("dealing_ranges", [])
    L += ["", "## Structural dealing ranges + Premium/Discount (EXPERIMENTAL — per-TF hierarchy)",
          "", "_Detector discovers the objective range on each structural TF; CONTEXT (later) "
          "decides which is relevant. Every downstream read states its dealing-range source._", ""]
    if drs:
        L += ["| TF | range | direction | CE/EQ |", "|---|---|---|---|"]
        for dr in drs:
            L.append(f"| {dr['source_tf']} | {dr['low']:g} – {dr['high']:g} | {dr['direction']} "
                     f"| {dr['ce']:g} |")
    else:
        L.append("_no dealing range (need ≥1 structural high and low)_")
    sweeps = r.get("ranked_sweeps", [])
    order = sweeps[0]["factor_order"] if sweeps else []
    L += ["", "## Manipulation — liquidity sweeps, RANKED not filtered (EXPERIMENTAL)", "",
          "_All objectively-valid sweeps are retained; ranking is lexicographic over modular "
          "factor evaluators (no hidden weights). Priority order: "
          + ", ".join(f"`{p}`" for p in order) + "._", ""]
    if sweeps:
        L += ["| rank | id | dir | pool | ext | " + " | ".join(order) + " |",
              "|---|---|---|---|---|" + "|".join("---" for _ in order) + "|"]
        for sw in sweeps:
            vals = " | ".join(f"{sw['factors'][k]:g}" if isinstance(sw['factors'][k], float)
                              else str(sw['factors'][k]) for k in order)
            tie = " (TIE)" if sw["tied"] else ""
            L.append(f"| #{sw['rank']}{tie} | {sw['id']} | {sw['direction']} | {sw['pool_price']:g} "
                     f"| {sw['extreme']:g} | {vals} |")
        L += ["", "### Pairwise comparison (why each candidate lost to the one above)", ""]
        for sw in sweeps:
            if sw["rank"] == 1:
                L.append(f"- **#1 {sw['id']}** — top priority.")
            else:
                L.append(f"- **#{sw['rank']} {sw['id']}** lost to #{sw['rank']-1} because: "
                         f"{sw['lost_to_prev']}")
    else:
        L.append("_no manipulation (no active ERL raided-and-rejected at this cursor)_")
    disp = r.get("ranked_displacements", [])
    dorder = disp[0]["factor_order"] if disp else []
    L += ["", "## Displacement — impulse legs, RANKED not filtered (EXPERIMENTAL)", "",
          "_From manipulation extreme to first width-1 counter-pivot (B5). Priority: "
          + ", ".join(f"`{p}`" for p in dorder) + "._", ""]
    if disp:
        L += ["| rank | id | dir | " + " | ".join(dorder) + " | depends_on |",
              "|---|---|---|" + "|".join("---" for _ in dorder) + "|---|"]
        for d in disp:
            vals = " | ".join(f"{d['factors'][k]:g}" if isinstance(d['factors'][k], float)
                              else str(d['factors'][k]) for k in dorder)
            tie = " (TIE)" if d["tied"] else ""
            L.append(f"| #{d['rank']}{tie} | {d['id']} | {d['direction']} | {vals} "
                     f"| {', '.join(d['depends_on'])} |")
        L += ["", "### Pairwise comparison", ""]
        for d in disp:
            if d["rank"] == 1:
                L.append(f"- **#1 {d['id']}** — top priority.")
            else:
                L.append(f"- **#{d['rank']} {d['id']}** lost to #{d['rank']-1} because: {d['lost_to_prev']}")
    else:
        L.append("_no displacement (no manipulation produced a directional impulse at this cursor)_")
    mss = r.get("ranked_mss", [])
    morder = mss[0]["factor_order"] if mss else []
    L += ["", "## MSS — market structure shifts, RANKED not filtered (EXPERIMENTAL)", "",
          "_Body close through the pre-manipulation structural swing (B6); states "
          "potential→candidate→confirmed. Priority: " + ", ".join(f"`{p}`" for p in morder) + "._", ""]
    if mss:
        L += ["| rank | id | dir | state | broken | " + " | ".join(morder) + " | depends_on |",
              "|---|---|---|---|---|" + "|".join("---" for _ in morder) + "|---|"]
        for m in mss:
            vals = " | ".join(f"{m['factors'][k]:g}" if isinstance(m['factors'][k], float)
                              else str(m['factors'][k]) for k in morder)
            tie = " (TIE)" if m["tied"] else ""
            L.append(f"| #{m['rank']}{tie} | {m['id']} | {m['direction']} | {m['state']} "
                     f"| {m['broken_price']:g} | {vals} | {', '.join(m['depends_on'])} |")
        L += ["", "### Pairwise comparison", ""]
        for m in mss:
            if m["rank"] == 1:
                L.append(f"- **#1 {m['id']}** — top priority.")
            else:
                L.append(f"- **#{m['rank']} {m['id']}** lost to #{m['rank']-1} because: {m['lost_to_prev']}")
    else:
        L.append("_no MSS (no displacement broke a pre-manipulation structural swing)_")
    fvgs = r.get("ranked_fvgs", [])
    forder = fvgs[0]["factor_order"] if fvgs else []
    L += ["", "## FVG — fair value gaps, RANKED not filtered (EXPERIMENTAL)", "",
          "_A1 geometry, inside the displacement leg that made the MSS (A5/B5); CE = entry ref "
          "(B8). Priority: " + ", ".join(f"`{p}`" for p in forder) + "._", ""]
    if fvgs:
        L += ["| rank | id | dir | status | CE | " + " | ".join(forder) + " | depends_on |",
              "|---|---|---|---|---|" + "|".join("---" for _ in forder) + "|---|"]
        for f in fvgs:
            vals = " | ".join(f"{f['factors'][k]:g}" if isinstance(f['factors'][k], float)
                              else str(f['factors'][k]) for k in forder)
            tie = " (TIE)" if f["tied"] else ""
            L.append(f"| #{f['rank']}{tie} | {f['id']} | {f['direction']} | {f['status']} "
                     f"| {f['ce']:g} | {vals} | {', '.join(f['depends_on'])} |")
        L += ["", "### Pairwise comparison", ""]
        for f in fvgs:
            if f["rank"] == 1:
                L.append(f"- **#1 {f['id']}** — top priority.")
            else:
                L.append(f"- **#{f['rank']} {f['id']}** lost to #{f['rank']-1} because: {f['lost_to_prev']}")
    else:
        L.append("_no FVG in any MSS-producing displacement leg at this cursor_")
    setups = r.get("ranked_setups", [])
    L += ["", "## Setups (Entry/Stop/Target) — RANKED not filtered (EXPERIMENTAL)", "",
          "_entry=FVG CE (B8); stop=manipulation extreme (A8); target=next opposing active ERL, "
          "RR≥" + f"{__import__('ict_live.config', fromlist=['MIN_RR']).MIN_RR}" + " (B4). "
          "Priority: actionable, rr, tight_risk._", ""]
    if setups:
        L += ["| rank | id | dir | entry | stop | target | RR | actionable | depends_on |",
              "|---|---|---|---|---|---|---|---|---|"]
        for s in setups:
            tgt = f"{s['target']:g}" if s['target'] is not None else "—"
            act = "✓" if s["actionable"] else f"✗ {s['reject_reason']}"
            L.append(f"| #{s['rank']} | {s['id']} | {s['direction']} | {s['entry']:g} | {s['stop']:g} "
                     f"| {tgt} | {s['rr']} | {act} | {', '.join(s['depends_on'])} |")
    else:
        L.append("_no setup candidates (no FVG with a traceable manipulation at this cursor)_")
    L += ["", "## Decision log — WHY + DEPENDS_ON (traceable to raw structure)", ""]
    for d in r.get("decisions", []):
        dep = f"  ⟵ depends on: {', '.join(d['depends_on'])}" if d.get("depends_on") else ""
        L.append(f"- `{d.get('id','')}` **{d['object']}** — {d['why']}{dep}")
    if r.get("comparison"):
        cmp = r["comparison"]
        L += ["", "## Course comparison (engine structural swings vs course labels)", "",
              f"- course-labelled swings: {cmp['course_total']}",
              f"- matched by engine: **{cmp['matched']}/{cmp['course_total']}**",
              f"- MISSED (course said significant, engine did not): {len(cmp['missed'])}"]
        for m in cmp["missed"]:
            L.append(f"  - {m['kind']} {m['price']:g} {m.get('label','')}")
        L.append(f"- EXTRA (engine significant, not course-labelled): {len(cmp['extras'])}")
        for e in cmp["extras"]:
            L.append(f"  - {e['kind']} {e['price']:g} @ {e['time']}")
    L += ["", "## Primitive coverage (what the engine draws today vs pending)", "",
          "| primitive | status |", "|---|---|"]
    for role in overlay.ROLE_ORDER:
        if counts.get(role, 0) > 0:
            L.append(f"| {role} | ✅ produced ({counts[role]}) |")
        else:
            L.append(f"| {role} | ⏳ pending — {_PENDING.get(role, 'later increment')} |")
    L += ["", "## Rejection reasons", "",
          "_N/A — the setup/state-machine engine is a later increment; no candidates are "
          "accepted or rejected yet. This section fills in once B5–B11 logic lands._",
          "", "> These candidate pivots are the raw fractal output. Use this view to settle the "
          "`sig_swing` definition against the course examples — do NOT freeze a rule from a single "
          "chart.", ""]
    p = RESULTS / f"visual_audit_{r['symbol'].replace(':', '_').replace('!', '')}_{stamp}.md"
    p.write_text("\n".join(L))
    return str(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MNQ1!")
    ap.add_argument("--date", required=True, help="ISO date (a past trading day) to replay to")
    ap.add_argument("--timeframe", default="60", help="TV resolution: 15, 60, 240, D ...")
    ap.add_argument("--steps", type=int, default=0, help="replay steps forward from the date")
    ap.add_argument("--out-name", default=None, help="screenshot filename (without .png)")
    ap.add_argument("--annotations", default=None, help="course-annotation JSON to overlay+compare")
    ap.add_argument("--no-rejected", action="store_true", help="hide rejected (noise) pivots")
    ap.add_argument("--no-swept", action="store_true", help="hide already-swept ERL levels")
    ap.add_argument("--tv-cli", default=None)
    ap.add_argument("--tv-cwd", default=None)
    a = ap.parse_args(argv)
    tv = TvClient(binary=a.tv_cli, cwd=a.tv_cwd)
    h = tv.health()
    if not (h.ok and isinstance(h.data, dict) and h.data.get("cdp_connected")):
        print("[visual_audit] MCP not reachable — launch TradingView with the debug port and retry.")
        return 2
    res = visual_audit(tv, a.symbol, a.date, timeframe=a.timeframe, steps=a.steps,
                       out_name=a.out_name, annotations_path=a.annotations,
                       show_rejected=not a.no_rejected, show_swept=not a.no_swept)
    tc, lc, drs = res["tier_counts"], res["liq_counts"], res.get("dealing_ranges", [])
    dr_str = ", ".join(f"{d['source_tf']}:{d['low']:g}-{d['high']:g}" for d in drs) or "none"
    print(f"[visual_audit] {res['n_bars']} bars | {sum(tc.values())} candidate / "
          f"{tc['rejected']} rejected / {tc['structural']} structural | "
          f"ERL {lc['active']} active / {lc['swept']} swept | DR[{dr_str}] | "
          f"manip {len(res.get('ranked_sweeps', []))} | "
          f"disp {len(res.get('ranked_displacements', []))} | "
          f"mss {len(res.get('ranked_mss', []))} | "
          f"fvg {len(res.get('ranked_fvgs', []))} | "
          f"setups {len(res.get('ranked_setups', []))} | "
          f"{res['render']['count']} shapes drawn")
    rec = res.get("recommendation", {})
    print(f"[visual_audit] RECOMMENDATION → {rec.get('decision')}: {rec.get('reason','')[:160]}")
    if res.get("comparison"):
        c = res["comparison"]
        print(f"[visual_audit] course match: {c['matched']}/{c['course_total']} "
              f"(missed {len(c['missed'])}, extra {len(c['extras'])})")
    print(f"[visual_audit] report: {res['report']}")
    print(f"[visual_audit] screenshot: {res['screenshot']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
