"""HRLR / LRLR liquidity-run classifier — STANDALONE, READ-ONLY.

A faithful Python port of the Pine Script indicator "High/Low Resistance Liquidity" (HRLR/LRLR).
It is a self-contained MODEL: it imports nothing from the trading engine, has no side effects, and
never influences trade generation, filters, or frequency. A "main script" (e.g. the scenario layer)
calls `detect_liquidity_runs(bars, ...)` and receives the classified, mitigation-tracked pools.

Classification (identical to the indicator), each pivot compared to the immediately-preceding pivot
of the SAME side:
  • STANDARD (0) — a plain unmitigated pivot: a lower high / higher low than the previous pivot,
    beyond the equal-level tolerance.
  • LRLR (1) — "Low Resistance": an EQUAL high/low — |Δ| ≤ tolerance ticks vs the previous pivot
    (EQH / EQL). This is the equal-highs/lows level the ICT course teaches but gives no number for;
    the tolerance here is the indicator's own parameter (user-supplied), not one invented by the engine.
  • HRLR (2) — "High Resistance": a SWEEP — a higher high (bearish) / lower low (bullish) than the
    previous pivot.

Mitigation: a HIGH pool is mitigated when a later bar's high ≥ its level; a LOW pool when a later
bar's low ≤ its level (matching the indicator's real-time mitigation engine). Everything is causal:
a pivot is only known `right` bars after it forms, and mitigation is scanned only from there.

Works on either engine `Bar` objects (`.high/.low/.open_time`) or snapshot dicts (`{'h','l','t'}`),
so the same model runs from the engine buffers or from the served snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

STANDARD, LRLR, HRLR = 0, 1, 2
_KIND = {STANDARD: "STANDARD", LRLR: "LRLR", HRLR: "HRLR"}


# ── bar accessors (accept Bar objects or {'o','h','l','c','t'} dicts) ─────────
def _h(b):
    return float(b.high if hasattr(b, "high") else b["h"])


def _l(b):
    return float(b.low if hasattr(b, "low") else b["l"])


def _t(b):
    if hasattr(b, "open_time"):
        return b.open_time.isoformat()
    return b.get("t") if isinstance(b, dict) else None


@dataclass
class LiquidityRun:
    price: float
    pivot_index: int
    pivot_time: Optional[str]
    is_high: bool
    pool_type: int                 # 0 STANDARD | 1 LRLR | 2 HRLR
    mitigated: bool
    mitigation_index: Optional[int]
    mitigation_time: Optional[str]

    @property
    def kind(self) -> str:
        return _KIND[self.pool_type]

    @property
    def side(self) -> str:
        return "BSL" if self.is_high else "SSL"        # buy-side above / sell-side below

    @property
    def bias(self) -> str:
        # a swept HIGH points bearish; a swept LOW points bullish (indicator's colHRLRbear/bull)
        if self.pool_type == HRLR:
            return "bearish" if self.is_high else "bullish"
        return ""

    def to_dict(self) -> dict:
        return {"price": round(self.price, 2), "kind": self.kind, "side": self.side,
                "is_high": self.is_high, "bias": self.bias, "mitigated": self.mitigated,
                "pivot_index": self.pivot_index, "pivot_time": self.pivot_time,
                "mitigation_index": self.mitigation_index, "mitigation_time": self.mitigation_time}


def _pivots(bars, left: int, right: int, high_side: bool) -> list[tuple[int, float]]:
    """Structural pivots à la `ta.pivothigh` / `ta.pivotlow`: the candidate `right` bars back must be
    STRICTLY beyond every one of the `left` bars before and `right` bars after it (ties disqualify)."""
    out: list[tuple[int, float]] = []
    n = len(bars)
    val = _h if high_side else _l
    for p in range(left, n - right):
        v = val(bars[p])
        ok = True
        for j in range(p - left, p + right + 1):
            if j == p:
                continue
            nj = val(bars[j])
            if (high_side and nj >= v) or (not high_side and nj <= v):
                ok = False
                break
        if ok:
            out.append((p, v))
    return out


def _mitigation(bars, p: int, right: int, price: float, high_side: bool):
    """First bar AFTER confirmation (p+right) that trades through `price` (high≥ / low≤). Causal."""
    for k in range(p + right + 1, len(bars)):
        if (high_side and _h(bars[k]) >= price) or (not high_side and _l(bars[k]) <= price):
            return k, _t(bars[k])
    return None, None


def _side_runs(bars, left, right, tol_ticks, tick, high_side) -> list[LiquidityRun]:
    runs: list[LiquidityRun] = []
    prev = None
    for p, v in _pivots(bars, left, right, high_side):        # already in confirmation order (by index)
        ptype = STANDARD
        if prev is not None:
            if tick > 0 and abs(v - prev) / tick <= tol_ticks:
                ptype = LRLR                                  # equal high/low (LRLR precedence, as in Pine)
            elif (high_side and v > prev) or (not high_side and v < prev):
                ptype = HRLR                                  # sweep of the previous pivot
        prev = v
        mi, mt = _mitigation(bars, p, right, v, high_side)
        runs.append(LiquidityRun(price=v, pivot_index=p, pivot_time=_t(bars[p]), is_high=high_side,
                                 pool_type=ptype, mitigated=mi is not None,
                                 mitigation_index=mi, mitigation_time=mt))
    return runs


def detect_liquidity_runs(bars, *, left: int = 5, right: int = 2, tol_ticks: int = 10,
                          tick: float = 0.25) -> list[LiquidityRun]:
    """Classify every structural pivot high/low into STANDARD / LRLR / HRLR and track mitigation.

    Defaults mirror the indicator (pivotLeft=5, pivotRight=2, EQH/EQL tolerance=10 ticks). `tick` is
    the instrument's min tick (0.25 for MNQ/MES). Pure and read-only — safe to call every refresh."""
    if not bars or len(bars) <= left + right:
        return []
    runs = _side_runs(bars, left, right, tol_ticks, tick, True) + \
        _side_runs(bars, left, right, tol_ticks, tick, False)
    runs.sort(key=lambda r: r.pivot_index)
    return runs


def summary(runs: list[LiquidityRun]) -> dict:
    """Counts + the unmitigated pools, for a panel/API. Read-only."""
    def n(t, mit=None):
        return sum(1 for r in runs if r.pool_type == t and (mit is None or r.mitigated == mit))
    return {
        "total": len(runs),
        "lrlr": n(LRLR), "hrlr": n(HRLR), "standard": n(STANDARD),
        "unmitigated": [r.to_dict() for r in runs if not r.mitigated],
        "pools": [r.to_dict() for r in runs],
    }
