"""Engine interpretation -> TradingView drawings (DEV-ONLY, one-way).

The engine emits typed objects (Swing, Pool, and — as later increments land — DealingRange,
FVG, Setup...). This module translates those into a neutral `Annotation` list and renders them
via the `tv draw shape` CLI. The engine never imports this; drawing is a pure VIEW of engine
state. Every shape carries a (time, price) anchor (the MCP requires a finite time on all shapes,
confirmed in Phase 0).

Coverage is the full 15-primitive target so nothing needs reworking as the engine grows; the
adapters populate only what the engine currently produces. `roles_present()` / the audit report
say which primitives are live vs still gated on the `sig_swing` freeze.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# ---- role -> shape kind + style overrides (cosmetic; TV ignores unknown keys) ----
# colors chosen for legibility on a dark chart; tuned once against a live screenshot.
_R = {
    "swing_high":     ("text",  {"color": "#ef5350", "fontsize": 11, "bold": True}),
    "swing_low":      ("text",  {"color": "#26a69a", "fontsize": 11, "bold": True}),
    # objective structural swings + contextual liquidity relevance
    "swing_structural":  ("text", {"color": "#b0bec5", "fontsize": 10}),
    "swing_dominant":    ("text", {"color": "#eceff1", "fontsize": 11, "bold": True}),
    "swing_rejected":    ("text", {"color": "#546e7a", "fontsize": 8}),
    "erl_active":     ("hline", {"linecolor": "#ffb300", "linewidth": 2, "linestyle": 0}),
    "erl_swept":      ("hline", {"linecolor": "#5d4037", "linewidth": 1, "linestyle": 2}),
    # manually-annotated course reference swings
    "course_high":    ("text",  {"color": "#ffffff", "fontsize": 11, "bold": True}),
    "course_low":     ("text",  {"color": "#ffffff", "fontsize": 11, "bold": True}),
    "erl_pool":       ("hline", {"linecolor": "#ff9800", "linewidth": 1, "linestyle": 0}),
    "irl_pool":       ("hline", {"linecolor": "#42a5f5", "linewidth": 1, "linestyle": 2}),
    "dealing_range":  ("rect",  {"color": "#78909c", "backgroundColor": "rgba(120,144,156,0.06)",
                                 "fillBackground": True}),
    "premium":        ("hline", {"linecolor": "#ef9a9a", "linestyle": 2}),
    "discount":       ("hline", {"linecolor": "#a5d6a7", "linestyle": 2}),
    "equilibrium":    ("hline", {"linecolor": "#bdbdbd", "linestyle": 2}),
    "sweep":          ("text",  {"color": "#ffee58", "fontsize": 11}),
    "manip_extreme":  ("hline", {"linecolor": "#ffca28", "linewidth": 2}),
    "displacement":   ("trend", {"linecolor": "#ab47bc", "linewidth": 2}),
    "mss":            ("hline", {"linecolor": "#7e57c2", "linewidth": 2, "linestyle": 0}),
    "mss_close":      ("text",  {"color": "#7e57c2", "fontsize": 11}),
    "fvg":            ("rect",  {"color": "#5c6bc0", "backgroundColor": "rgba(92,107,192,0.18)",
                                 "fillBackground": True}),
    "entry":          ("hline", {"linecolor": "#66bb6a", "linewidth": 2}),
    "stop":           ("hline", {"linecolor": "#ef5350", "linewidth": 2}),
    "target":         ("hline", {"linecolor": "#29b6f6", "linewidth": 2}),
    "target_liq":     ("hline", {"linecolor": "#29b6f6", "linestyle": 2}),
    "amd":            ("text",  {"color": "#ffffff", "fontsize": 11}),
    "htf":            ("text",  {"color": "#90a4ae", "fontsize": 11}),
    "rejection":      ("text",  {"color": "#bdbdbd", "fontsize": 10, "italic": True}),
}

# the full target set, in report order, with a human label
ROLE_ORDER = ["swing_high", "swing_low", "erl_pool", "irl_pool", "dealing_range",
              "premium", "discount", "equilibrium", "sweep", "manip_extreme",
              "displacement", "mss", "mss_close", "fvg", "entry", "stop", "target",
              "target_liq", "amd", "htf", "rejection"]

_KIND_LABEL = {"high": "SH", "low": "SL"}


@dataclass
class Annotation:
    role: str
    price: float
    time: int                       # unix seconds (anchor)
    price2: Optional[float] = None
    time2: Optional[int] = None
    text: Optional[str] = None

    def kind(self) -> str:
        return _R[self.role][0]

    def overrides(self) -> dict:
        return dict(_R[self.role][1])


def _unix(dt: datetime) -> int:
    return int(dt.timestamp())


# ---------- engine-object adapters (only what the engine produces today) ----------
def from_swings(swings, *, label_high="SH", label_low="SL") -> list[Annotation]:
    out = []
    for s in swings:
        role = "swing_high" if s.kind == "high" else "swing_low"
        lab = label_high if s.kind == "high" else label_low
        out.append(Annotation(role=role, price=float(s.price), time=_unix(s.time),
                              text=f"{lab} {s.price:g}"))
    return out


def from_structural(classified, *, show_rejected: bool = True) -> list[Annotation]:
    """Objective structural swings -> markers. `dominant` (degree-2) rendered brighter, but this
    is a structural-degree diagnostic, NOT a significance verdict (that is liquidity's job)."""
    out = []
    for cs in classified:
        s = cs.swing
        lab = _KIND_LABEL[s.kind]
        if cs.tier == "structural":
            role = "swing_dominant" if cs.dominant else "swing_structural"
            out.append(Annotation(role, float(s.price), _unix(s.time), text=f"{lab} {s.price:g}"))
        elif show_rejected:
            out.append(Annotation("swing_rejected", float(s.price), _unix(s.time), text="·"))
    return out


def from_swing_liquidity(pools, *, show_swept: bool = True) -> list[Annotation]:
    """Structural-swing ERL pools -> horizontal levels; active (unswept) bright, swept dim."""
    out = []
    for p in pools:
        if p.swept and not show_swept:
            continue
        role = "erl_swept" if p.swept else "erl_active"
        side = "BSL" if p.kind == "high" else "SSL"     # buy-/sell-side liquidity
        tag = "swept" if p.swept else "active"
        out.append(Annotation(role, float(p.price), _unix(p.time),
                              text=f"ERL {side} {p.price:g} ({tag})"))
    return out


def from_course(items: list[dict]) -> list[Annotation]:
    """Manually-annotated course swings. Each item: {kind:'high'|'low', time:unix_int,
    price:float, label?:str}. Rendered distinctly (white) for side-by-side comparison."""
    out = []
    for it in items:
        role = "course_high" if it["kind"] == "high" else "course_low"
        mark = "▲" if it["kind"] == "high" else "▼"
        lab = it.get("label", "")
        out.append(Annotation(role, float(it["price"]), int(it["time"]),
                              text=f"C{mark}{(' ' + lab) if lab else ''}"))
    return out


def from_dealing_ranges(ranges) -> list[Annotation]:
    """Every discovered structural dealing range (one per TF). Each object is TAGGED with its
    source TF so downstream reads state which range they use — no single 'active' range."""
    out = []
    for dr in ranges:
        if dr is None:
            continue
        tf = dr.source_tf
        t_lo, t_hi = _unix(dr.low_time), _unix(dr.high_time)
        t0, t1 = min(t_lo, t_hi), max(t_lo, t_hi)
        out += [
            Annotation("dealing_range", dr.low, t0, price2=dr.high, time2=t1),
            Annotation("equilibrium", dr.ce, t1, text=f"CE/EQ({tf}) {dr.ce:g} [{dr.direction}]"),
            Annotation("premium", dr.high, t1, text=f"PREMIUM({tf}) ≤{dr.high:g}"),
            Annotation("discount", dr.low, t1, text=f"DISCOUNT({tf}) ≥{dr.low:g}"),
        ]
    return out


def from_ranked_sweeps(ranked) -> list[Annotation]:
    """Ranked liquidity sweeps (manipulation): marker shows the RANK (#1 = top priority) so the
    chart displays the ranking, not a filtered subset. All candidates are drawn."""
    out = []
    for r in ranked:
        s = r.item
        arrow = "↓" if s.direction == "bearish" else "↑"
        tie = "=" if r.tied else ""
        out.append(Annotation("sweep", float(s.extreme), _unix(s.time),
                              text=f"#{r.rank}{tie} SWEEP{arrow} {s.direction} @ {s.pool_price:g}"))
        out.append(Annotation("manip_extreme", float(s.extreme), _unix(s.time),
                              text=f"manip.ext {s.extreme:g}"))
    return out


def from_ranked_displacements(ranked, bars) -> list[Annotation]:
    """Ranked displacement legs as trend lines from manipulation extreme to impulse exhaustion.
    Marker shows rank (#1 = strongest). All candidates drawn (rank, not filter)."""
    out = []
    for r in ranked:
        d = r.item
        t0 = int(bars[d.start_index].open_time.timestamp())
        t1 = int(bars[d.end_index].open_time.timestamp())
        arrow = "↓" if d.direction == "bearish" else "↑"
        out.append(Annotation("displacement", d.start_price, t0, price2=d.end_price, time2=t1,
                              text=f"#{r.rank} DISP{arrow} net={d.net:g}"))
    return out


def from_ranked_mss(ranked, bars) -> list[Annotation]:
    """Ranked MSS: a level line at the broken structural swing (+ a close marker when confirmed).
    Rank shown; all candidates drawn (rank, not filter)."""
    out = []
    for r in ranked:
        m = r.item
        arrow = "↓" if m.direction == "bearish" else "↑"
        t_break = int(bars[m.broken_index].open_time.timestamp())
        out.append(Annotation("mss", m.broken_price, t_break,
                              text=f"#{r.rank} MSS{arrow} {m.state} @ {m.broken_price:g}"))
        if m.confirm_index is not None:
            out.append(Annotation("mss_close", float(bars[m.confirm_index].close),
                                  int(bars[m.confirm_index].open_time.timestamp()),
                                  text=f"MSS close {bars[m.confirm_index].close:g}"))
    return out


def from_ranked_fvgs(ranked, bars) -> list[Annotation]:
    """Ranked FVGs as gap rectangles + a CE line. Rank shown; all drawn (rank, not filter)."""
    out = []
    for r in ranked:
        f = r.item
        t0 = int(bars[f.mid_index - 1].open_time.timestamp())
        t1 = int(bars[min(f.mid_index + 1, len(bars) - 1)].open_time.timestamp())
        out.append(Annotation("fvg", f.bottom, t0, price2=f.top, time2=t1,
                              text=f"#{r.rank} FVG {f.direction} {f.status}"))
        out.append(Annotation("equilibrium", f.ce, t1, text=f"FVG CE {f.ce:g}"))
    return out


def from_ranked_setups(ranked, ref_time: int) -> list[Annotation]:
    """Ranked setups: entry/stop/target level lines (green/red/blue), rank + actionable shown."""
    out = []
    for r in ranked:
        s = r.item
        tag = "✓" if s.actionable else "✗"
        out.append(Annotation("entry", s.entry, ref_time,
                              text=f"#{r.rank}{tag} {s.direction.upper()} ENTRY {s.entry:g} (RR {s.rr})"))
        out.append(Annotation("stop", s.stop, ref_time, text=f"STOP {s.stop:g}"))
        if s.target is not None:
            out.append(Annotation("target", s.target, ref_time, text=f"TARGET {s.target:g}"))
    return out


def from_pools(pools, ref_time: int) -> list[Annotation]:
    """Objective liquidity pools -> horizontal levels. `ref_time` is any on-screen bar time
    (the level spans the chart; the anchor only positions the label)."""
    out = []
    for p in pools:
        role = "erl_pool" if getattr(p, "erl", True) else "irl_pool"
        out.append(Annotation(role=role, price=float(p.price), time=ref_time,
                              text=f"{p.name} {p.price:g}"))
    return out


def roles_present(annotations: list[Annotation]) -> dict[str, int]:
    counts = {r: 0 for r in ROLE_ORDER}
    for a in annotations:
        counts[a.role] = counts.get(a.role, 0) + 1
    return counts


# ---------- render ----------
def _args(a: Annotation) -> list[str]:
    kind = a.kind()
    args = ["draw", "shape", "-t", kind, "-p", f"{a.price}", "--time", f"{a.time}"]
    if kind in ("rect", "rectangle", "trend", "trend_line"):
        # map our short kind to the CLI's type name
        args[3] = "rectangle" if kind.startswith("rect") else "trend_line"
        args += ["--price2", f"{a.price2}", "--time2", f"{a.time2}"]
    elif kind == "hline":
        args[3] = "horizontal_line"
    if a.text is not None:
        args += ["--text", a.text]
    ov = a.overrides()
    if ov:
        args += ["--overrides", json.dumps(ov)]
    return args


def render(client, annotations: list[Annotation], *, clear: bool = True) -> dict:
    """Draw annotations on the current chart. Returns {entity_ids, errors}. Never raises."""
    if clear:
        client.draw_clear()
    ids, errors = [], []
    for a in annotations:
        r = client.run(*_args(a))
        data = r.data if isinstance(r.data, dict) else {}
        if r.ok and data.get("success"):
            ids.append(data.get("entity_id"))
        else:
            errors.append({"role": a.role, "error": r.error or (data.get("error") if data else r.stderr[:160])})
    return {"entity_ids": ids, "errors": errors, "count": len(ids)}
