"""Execution-quality layer (v1) — a TRANSPARENT DETERMINISTIC score on top of the (frozen) engine.

The reasoning engine finds the setup and produces the STRUCTURAL recommendation (LONG/SHORT). This
layer answers a separate question — is a structurally-valid setup worth EXECUTING? — from five
transparent factors of the current winning setup:

  1. pd_location       favorable-side alignment (long in discount / short in premium)
  2. ce_distance       proximity to CE (penalize entries pushed far into the imbalance)
  3. rr_realism        realistic reward:risk (very high RR often = wide target + mediocre entry)
  4. confirmation      MSS state + displacement exhaustion + sweep rejection strength
  5. fvg_location      entry-FVG quality (unfilled/touched, size sane vs the range)

Each factor is in [0,1] (higher = better execution). v1 is a FIXED, HAND-SET weighted mean — NO ML,
no fitted/loaded coefficients, no hidden weights. The weights below were chosen from a factor-
separation study of 102 human labels (Batch-1 + Batch-2, see research/RESULT_factor_separation.md):
premium/discount LOCATION is the dominant reason a structurally-valid setup gets rejected, and CE
distance is the only meaningful independent secondary; confirmation / rr_realism / fvg_location did
not separate TRADE from PASS, so they are computed and shown but not scored. NOTHING here changes
engine decisions; it only ADDS the TRADE/PASS recommendation + confidence + reasons.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FACTOR_NAMES = ["pd_location", "ce_distance", "rr_realism", "confirmation", "fvg_location"]

# v1 execution score = 0.6 * pd_location + 0.4 * ce_distance (others computed/shown, not scored).
V1_WEIGHTS = {"pd_location": 0.6, "ce_distance": 0.4,
              "rr_realism": 0.0, "confirmation": 0.0, "fvg_location": 0.0}
V1_THRESHOLD = 0.39

# Execution EXIT model — FROZEN after locked-OOS confirmation (2026-08-22). The mechanical exit is a
# full exit at +2R; the engine's structural liquidity target remains an ANALYTICAL objective, not the
# exit. Justification: exit-model + stop characterization studies (RESULT_exit_models.md /
# RESULT_stop_analysis.md) + locked-OOS PASS (RESULT_exit_models_OOS.md: win 0.60, +0.79R, no fat
# tail). Entry (FVG CE) and stop (manipulation extreme, −1R) are unchanged from the engine.
EXIT_MODEL = "fixed_2R"
EXIT_TARGET_R = 2.0


FACTOR_ISSUE = {
    "pd_location": "weak location — entry on the wrong premium/discount side",
    "ce_distance": "entry far from CE (deep into the imbalance)",
    "rr_realism": "RR realism poor — likely inflated by a distant liquidity target",
    "confirmation": "insufficient confirmation (MSS / displacement / sweep)",
    "fvg_location": "low-quality entry FVG",
}
_ISSUE_BAR = 0.5                    # a factor below this is surfaced as an explicit reason


@dataclass(frozen=True)
class ExecutionAssessment:
    structural: str                 # LONG / SHORT / NO-TRADE (from the engine, unchanged)
    execution: str                  # TRADE / PASS / N/A
    confidence: float               # execution_quality in [0,1]
    factors: dict                   # the 5 sub-scores
    reasons: tuple = ()             # human-readable per-factor issues (worst first)
    weakest_factor: str = ""
    reason: str = ""                # one-line summary


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def factors(ms) -> Optional[dict]:
    """Five execution-quality sub-scores for the current winning setup; None if no live trade."""
    win = ms.recommendation.setup
    if win is None or not ms.ranges:
        return None
    dr = ms.ranges[0]
    span = (dr.high - dr.low) or 1e-9
    ce = dr.ce
    long_ = win.direction == "long"

    # 1) favorable-side location: fraction into the FAVORABLE half (0 if on the wrong side)
    if long_:
        pd_location = _clamp((ce - win.entry) / ((ce - dr.low) or 1e-9))     # deep discount -> 1
    else:
        pd_location = _clamp((win.entry - ce) / ((dr.high - ce) or 1e-9))    # deep premium -> 1

    # 2) CE proximity: entries pushed far into the imbalance are penalized (extreme -> 0)
    ce_distance = _clamp(1.0 - abs(win.entry - ce) / (0.5 * span))

    # 3) RR realism: reward a realistic band ~[3,8]; decay for very wide (target-far/entry-mediocre)
    rr = win.rr
    rr_realism = 1.0 if 3.0 <= rr <= 8.0 else _clamp(1.0 - (rr - 8.0) / 24.0) if rr > 8 else _clamp(rr / 3.0)

    # 4) confirmation strength: MSS state + displacement exhaustion + sweep rejection
    fvg = _by_dep(ms.ranked_fvgs, win.depends_on, "FVG")
    mss = _by_dep(ms.ranked_mss, win.depends_on, "MSS")
    swp = _by_dep(ms.ranked_sweeps, win.depends_on, "SWP")
    disp = _by_dep(ms.ranked_displacements, fvg.depends_on, "DISP") if fvg else None
    mss_score = {"confirmed": 1.0, "candidate": 0.6, "potential": 0.2}.get(getattr(mss, "state", ""), 0.3)
    disp_score = 1.0 if (disp and disp.exhausted) else 0.5
    rej_score = _clamp((abs(swp.extreme - swp.pool_price) / (0.25 * span)) if swp else 0.0)
    confirmation = _clamp(0.5 * mss_score + 0.3 * disp_score + 0.2 * rej_score)

    # 5) FVG execution quality: unfilled best; size sane (not a huge coarse gap) relative to range
    if fvg is None:
        fvg_location = 0.3
    else:
        status_score = {"unfilled": 1.0, "touched": 0.6, "mitigated": 0.0}.get(fvg.status, 0.3)
        size_frac = abs(fvg.top - fvg.bottom) / span
        size_score = _clamp(1.0 - abs(size_frac - 0.05) / 0.15)   # sweet spot ~5% of the range
        fvg_location = _clamp(0.7 * status_score + 0.3 * size_score)

    return {"pd_location": round(pd_location, 4), "ce_distance": round(ce_distance, 4),
            "rr_realism": round(rr_realism, 4), "confirmation": round(confirmation, 4),
            "fvg_location": round(fvg_location, 4)}


def _by_dep(ranked, depends_on, prefix):
    want = next((d for d in depends_on if d.startswith(prefix)), None)
    if want is None:
        return None
    return next((r.item for r in ranked if r.item.id == want), None)


def assess(ms, *, weights: Optional[dict] = None,
           threshold: Optional[float] = None) -> ExecutionAssessment:
    """execution_quality = a TRANSPARENT weighted mean of the factors (v1: 0.6·pd_location +
    0.4·ce_distance), TRADE iff >= threshold. No ML, no fitted coefficients. Reasons and the weakest
    factor are drawn from the SCORED factors only, so the explanation matches the decision. The
    structural recommendation is passed through unchanged."""
    w = V1_WEIGHTS if weights is None else weights
    thr = V1_THRESHOLD if threshold is None else threshold
    structural = ms.recommendation.decision
    f = factors(ms)
    if f is None:
        return ExecutionAssessment(structural, "N/A", 0.0, {}, reason="no live setup to assess")
    tot = sum(w.get(k, 0.0) for k in FACTOR_NAMES) or 1.0
    q = round(sum(w.get(k, 0.0) * f[k] for k in FACTOR_NAMES) / tot, 4)
    execution = "TRADE" if q >= thr else "PASS"
    scored = [k for k in FACTOR_NAMES if w.get(k, 0.0) > 0] or FACTOR_NAMES
    worst = min(scored, key=lambda k: f[k])
    reasons = tuple(f"{FACTOR_ISSUE[k]} ({k}={f[k]})"
                    for k in sorted(scored, key=lambda k: f[k]) if f[k] < _ISSUE_BAR)
    reason = (f"execution_quality {q} ({'>=' if execution == 'TRADE' else '<'} {thr}); "
              f"weakest scored factor: {worst}={f[worst]}")
    return ExecutionAssessment(structural, execution, q, f, reasons, worst, reason)
