"""Engine-rendered ICT chart (PNG) — candlesticks of a symbol's latest 1H MarketState with the
engine's own marks: dealing range + premium/discount/CE, active ERL liquidity (BSL/SSL ≈ equal
highs/lows), ranked FVGs, the top MSS level, the current setup's entry/stop/target, and the New
Week Opening Gap. Server-side, deterministic, READ-ONLY (never mutates the MarketState). No
TradingView. Operational/visual only — not part of the frozen decision path.
"""
from __future__ import annotations

import io

_BG = "#0b0e14"; _PANEL = "#131722"; _GRID = "#242a37"; _INK = "#e6e9ef"; _MUT = "#8b93a7"
_UP = "#26a69a"; _DN = "#ef5350"


def _nwog(bars):
    """(low, high, index) for the New Week Opening Gap = Friday close → Sunday open, located at the
    biggest time gap in the window (the weekend). None if the window spans no weekend."""
    if len(bars) < 3:
        return None
    best_i, best_gap = None, 0.0
    for i in range(1, len(bars)):
        gap = (bars[i].open_time - bars[i - 1].close_time).total_seconds()
        if gap > best_gap:
            best_gap, best_i = gap, i
    if best_i is None or best_gap < 12 * 3600:      # > ~12h ⇒ a weekend, not the daily break
        return None
    a, b = bars[best_i - 1].close, bars[best_i].open
    return (min(a, b), max(a, b), best_i)


def render_png(ms, bars, *, symbol="", title="", max_bars=140) -> bytes:
    """Render `bars` (the exact window `ms` was computed on) + `ms`'s marks to a PNG (bytes)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    n_full = len(bars)
    start = max(0, n_full - max_bars)
    view = bars[start:]
    n = len(view)
    xr = n - 1 + 4                                  # right margin (marks + labels live here)
    xi = lambda idx: idx - start                    # full-bars index → view x

    fig, ax = plt.subplots(figsize=(12.5, 6.6), dpi=110)
    fig.patch.set_facecolor(_BG); ax.set_facecolor(_PANEL)
    for sp in ax.spines.values():
        sp.set_color(_GRID)
    ax.tick_params(colors=_MUT, labelsize=8)
    ax.grid(True, color=_GRID, lw=0.4, alpha=0.5)

    w = 0.32
    for i, b in enumerate(view):
        c = _UP if b.close >= b.open else _DN
        ax.plot([i, i], [b.low, b.high], color=c, lw=0.7, zorder=3)
        lo, hi = sorted((b.open, b.close))
        ax.add_patch(Rectangle((i - w, lo), 2 * w, max(hi - lo, 1e-9), facecolor=c, edgecolor=c, zorder=4))

    span = (max(b.high for b in view) - min(b.low for b in view)) or 1.0
    _used = []
    def lbl(y, text, color, force=False):
        if not force and any(abs(y - uy) < span * 0.02 for uy in _used):
            return                                  # skip a label that would overprint a nearby one
        _used.append(y)
        ax.text(xr + 0.3, y, text, color=color, fontsize=7.5, va="center", ha="left", zorder=6)

    if ms.ranges:                                   # dealing range + premium/discount/CE (most recent)
        dr = ms.ranges[0]
        ax.axhspan(dr.low, dr.high, color="#78909c", alpha=0.06, zorder=1)
        ax.axhline(dr.ce, color="#bdbdbd", ls="--", lw=0.9, zorder=2); lbl(dr.ce, f"CE/EQ {dr.ce:g}", "#bdbdbd", force=True)
        ax.axhline(dr.high, color="#ef9a9a", ls=":", lw=0.8, zorder=2); lbl(dr.high, "premium", "#ef9a9a")
        ax.axhline(dr.low, color="#a5d6a7", ls=":", lw=0.8, zorder=2); lbl(dr.low, "discount", "#a5d6a7")

    for p in (ms.active_erl or [])[:8]:             # active ERL liquidity (≈ equal highs/lows)
        side = "BSL" if p.kind == "high" else "SSL"
        ax.axhline(p.price, color="#ffb300", lw=1.2, zorder=2); lbl(p.price, f"ERL {side} {p.price:g}", "#ffb300")

    for r in (ms.ranked_fvgs or [])[:3]:            # ranked FVGs as gap boxes
        f = r.item
        x0 = max(xi(f.formed_index), 0)
        ax.add_patch(Rectangle((x0, f.bottom), xr - x0, f.top - f.bottom, facecolor="#5c6bc0",
                               alpha=0.16, edgecolor="#5c6bc0", lw=0.6, zorder=1))
        lbl(f.top, f"FVG {f.direction} {f.status}", "#7986cb")

    if ms.ranked_mss:                               # top MSS level
        m = ms.ranked_mss[0].item
        ax.axhline(m.broken_price, color="#7e57c2", lw=1.1, zorder=2); lbl(m.broken_price, f"MSS {m.state}", "#9575cd")

    su = getattr(ms.recommendation, "setup", None)  # the current setup's entry/stop/target
    if su is not None:
        ax.axhline(su.entry, color="#66bb6a", lw=1.6, zorder=5); lbl(su.entry, f"entry {su.entry:g}", "#66bb6a", force=True)
        ax.axhline(su.stop, color="#ef5350", lw=1.6, zorder=5); lbl(su.stop, f"stop {su.stop:g}", "#ef5350", force=True)
        if su.target is not None:
            ax.axhline(su.target, color="#29b6f6", lw=1.2, ls="--", zorder=5); lbl(su.target, f"target {su.target:g}", "#29b6f6", force=True)

    ng = _nwog(bars)                                # New Week Opening Gap
    if ng:
        lo, hi, idx = ng
        x0 = max(xi(idx), 0)
        ax.add_patch(Rectangle((x0, lo), xr - x0, hi - lo, facecolor="#ffd54f", alpha=0.10,
                               edgecolor="#ffd54f", lw=0.7, ls="--", zorder=1))
        lbl(hi, "NWOG", "#ffd54f")

    ax.set_xlim(-1, xr + 8)
    dec = getattr(ms.recommendation, "decision", "")
    ax.set_title(f"{title or symbol}   ·   {ms.tf}   ·   {dec}", color=_INK, fontsize=11, loc="left")
    if n:
        step = max(1, n // 6)
        ticks = list(range(0, n, step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([view[i].open_time.strftime("%m-%d %H:%M") for i in ticks], fontsize=7.5)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()
