"""The pure, transport-free ICT engine — the SINGLE pipeline shared by the microscope
(devtools/tvmcp/audit), the causal replay/dataset generator, and (later) live operation.

`analyze(bars, tf)` runs every objective detector, ranks each candidate type with the
domain-agnostic ranking engine + modular factor evaluators, assembles setups, and produces a
recommendation — returning one immutable `MarketState`. No TradingView, no overlay, no I/O:
a deterministic, causal function of the closed bars up to the cursor. Running it on `bars[:k+1]`
is exactly the state the engine knew at bar k (prefix-stable / no look-ahead).

Nothing here is methodology-frozen beyond the course-resolved rules the detectors cite; the
`config` sentinels remain hard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ict_live import config as C
from ict_live.market.bar import Bar
from ict_live.structure import (dealing_range, displacement, fvg as fvg_mod, ids, lifecycle,
                                manipulation, mss as mss_mod, ranking, setup as setup_mod,
                                significance, swing_liquidity)
from ict_live.structure.swings import SwingDetector


@dataclass(frozen=True)
class MarketState:
    tf: str
    n_bars: int
    fractal_width: int
    classified: list          # list[significance.ClassifiedSwing]
    structural: list          # list[Swing]
    pools: list               # list[SwingPool]
    active_erl: list          # list[SwingPool]
    ranges: list              # list[DealingRange]
    ranked_sweeps: list       # list[ranking.Ranked[Sweep]]
    ranked_displacements: list
    ranked_mss: list
    ranked_fvgs: list
    ranked_setups: list
    recommendation: object    # setup_mod.Recommendation
    tier_counts: dict = field(default_factory=dict)
    liq_counts: dict = field(default_factory=dict)
    lifecycle: dict = field(default_factory=dict)   # historical/active/current separation


# ---------- modular factor evaluators (ranker stays domain-agnostic) ----------
def sweep_evaluators(classified, ranges, bars):
    dominant = {(cs.swing.kind, cs.swing.index) for cs in classified if cs.dominant}
    ce = ranges[0].ce if ranges else None

    def dominant_pool(sw):
        pk = "high" if sw.direction == "bearish" else "low"
        v = 1 if (pk, sw.pool_index) in dominant else 0
        return ranking.FactorValue("dominant_pool", v,
                                   "raided a DOMINANT structural swing" if v else
                                   "raided a non-dominant structural swing")

    def pd_aligned(sw):
        if ce is None:
            return ranking.FactorValue("pd_aligned", 0, "no dealing range → no P/D context")
        if sw.direction == "bearish":
            v = 1 if sw.pool_price > ce else 0
            e = f"buy-side raid {'in Premium' if v else 'not in Premium'} (CE {ce:g})"
        else:
            v = 1 if sw.pool_price < ce else 0
            e = f"sell-side raid {'in Discount' if v else 'not in Discount'} (CE {ce:g})"
        return ranking.FactorValue("pd_aligned", v, e)

    def rejection_strength(sw):
        wick = abs(sw.extreme - sw.pool_price) + 1e-9
        reclaim = (sw.pool_price - sw.close) if sw.direction == "bearish" else (sw.close - sw.pool_price)
        v = round(max(reclaim, 0.0) / wick, 3)
        return ranking.FactorValue("rejection_strength", v, f"close reclaimed {v}× the wick beyond the level")

    def recency(sw):
        return ranking.FactorValue("recency", sw.bar_index, f"raid at bar {sw.bar_index} (fresher = higher)")

    return [dominant_pool, pd_aligned, rejection_strength, recency]


def displacement_evaluators():
    def net_move(d):
        return ranking.FactorValue("net_move", round(d.net, 2), f"impulse magnitude {d.net:g}")

    def speed(d):
        v = round(d.net / max(d.span, 1), 3)
        return ranking.FactorValue("speed", v, f"{v} price/bar over {d.span} bars (energetic=higher)")

    def exhausted(d):
        return ranking.FactorValue("exhausted", 1 if d.exhausted else 0,
                                   "impulse completed at a counter-pivot" if d.exhausted
                                   else "still in progress at cursor")

    def recency(d):
        return ranking.FactorValue("recency", d.start_index, f"impulse starts at bar {d.start_index}")

    return [net_move, speed, exhausted, recency]


def mss_evaluators(classified):
    dominant = {(cs.swing.kind, cs.swing.index) for cs in classified if cs.dominant}
    _sr = {"confirmed": 2, "candidate": 1, "potential": 0}

    def state(m):
        return ranking.FactorValue("state", _sr[m.state], f"MSS state = {m.state}")

    def broken_dominant(m):
        kind = "low" if m.direction == "bearish" else "high"
        v = 1 if (kind, m.broken_index) in dominant else 0
        return ranking.FactorValue("broken_dominant", v,
                                   "broke a DOMINANT structural swing" if v else
                                   "broke a non-dominant structural swing")

    def acceptance(m):
        return ranking.FactorValue("acceptance", m.acceptance, f"close {m.acceptance:g} beyond the swing")

    def recency(m):
        return ranking.FactorValue("recency", m.broken_index, f"broken swing at bar {m.broken_index}")

    return [state, broken_dominant, acceptance, recency]


def fvg_evaluators():
    _st = {"unfilled": 2, "touched": 1, "mitigated": 0}

    def status(f):
        return ranking.FactorValue("status", _st[f.status], f"FVG status={f.status}")

    def size(f):
        v = round(f.top - f.bottom, 2)
        return ranking.FactorValue("size", v, f"gap size {v}")

    def recency(f):
        return ranking.FactorValue("recency", f.formed_index, f"formed at bar {f.formed_index}")

    return [status, size, recency]


def setup_evaluators():
    def actionable(s):
        return ranking.FactorValue("actionable", 1 if s.actionable else 0,
                                   "actionable" if s.actionable else f"rejected: {s.reject_reason}")

    def rr(s):
        return ranking.FactorValue("rr", s.rr, f"reward:risk {s.rr} to real liquidity")

    def risk_tight(s):
        return ranking.FactorValue("tight_risk", round(-s.risk, 2), f"risk {s.risk}")

    return [actionable, rr, risk_tight]


# ---------- the pipeline ----------
def analyze(bars: list[Bar], tf: str, *, width: Optional[int] = None,
            structural_by_tf: Optional[dict] = None,
            refine_bars: Optional[list[Bar]] = None,
            min_stop: Optional[float] = None) -> MarketState:
    """Full causal pass over `bars` (already truncated at the cursor). `structural_by_tf` lets a
    caller inject HTF structural swings for the multi-TF dealing-range hierarchy.

    MTF entry refinement (general mechanism): if `refine_bars` (a LOWER timeframe covering the same
    span, already truncated at the cursor) is given, entry FVGs are ALSO sought on that lower TF
    inside each HTF displacement — the HTF sweep/ERL/MSS are never redefined. Structure-TF FVGs are
    still detected, so a valid HTF entry is not forced onto a lower TF, and the ranker does not
    prefer the finer TF just to fill (it ranks by actionable/RR, TF-agnostic). Every setup records
    its structure TF and entry TF."""
    w = width if width is not None else C.FRACTAL_WIDTH.get(tf, 2)
    det = SwingDetector(w)
    for b in bars:
        det.add(b)
    swings = det.confirmed()
    classified = significance.classify(swings, bars)
    structural = significance.structural_swings(classified)
    pools = swing_liquidity.swing_liquidity(structural, bars)
    active_erl = swing_liquidity.active(pools)

    by_tf = dict(structural_by_tf) if structural_by_tf else {}
    by_tf.setdefault(tf, structural)
    ranges = dealing_range.dealing_ranges(by_tf)

    sweeps = manipulation.detect_sweeps(pools, bars)
    ranked_sweeps = ranking.rank(sweeps, sweep_evaluators(classified, ranges, bars))
    ranked_disp = ranking.rank(displacement.detect_displacements(sweeps, bars),
                               displacement_evaluators())
    disps = [r.item for r in ranked_disp]
    ranked_mss = ranking.rank(mss_mod.detect_mss(disps, structural, bars), mss_evaluators(classified))
    mss_items = [r.item for r in ranked_mss]
    disp_by_id = {d.id: d for d in disps}
    fvgs = fvg_mod.detect_fvgs(mss_items, disps, bars)                     # structure-TF entries
    if refine_bars:
        fvgs = fvgs + fvg_mod.detect_fvgs_mtf(mss_items, disp_by_id, bars, refine_bars)  # + LTF entries
    ranked_fvg = ranking.rank(fvgs, fvg_evaluators())

    sweep_by_id = {r.item.id: r.item for r in ranked_sweeps}
    dr_ident = ids.dr_id(ranges[0]) if ranges else None
    setups = setup_mod.build_setups([r.item for r in ranked_fvg], disp_by_id, sweep_by_id,
                                    active_erl, dr_ident, structure_tf=tf, min_stop=min_stop)
    ranked_setups = ranking.rank(setups, setup_evaluators())
    # lifecycle separation: only CURRENT COMPETITORS (actionable + not superseded/invalidated) may
    # compete for the recommendation; historical/active objects are retained but never rank here.
    cursor_time = bars[-1].open_time if bars else None
    life = lifecycle.classify(ranked_setups=ranked_setups, ranked_mss=ranked_mss,
                              ranked_sweeps=ranked_sweeps, ranked_displacements=ranked_disp,
                              ranked_fvgs=ranked_fvg, pools=pools, structural=structural,
                              cursor_time=cursor_time)
    # current_rank / current_pairwise recomputed FROM SCRATCH over current competitors only — the
    # global ranking is retained on each object as audit metadata but must not feed current views/ML.
    obj_state = life["object_state"]
    layer_evals = [(ranked_sweeps, sweep_evaluators(classified, ranges, bars), False),
                   (ranked_disp, displacement_evaluators(), False),
                   (ranked_mss, mss_evaluators(classified), False),
                   (ranked_fvg, fvg_evaluators(), False),
                   (ranked_setups, setup_evaluators(), True)]
    current_ranking = {}
    current_setups_ranked = []
    for ranked, evals, is_setup in layer_evals:
        cur_items = [r.item for r in ranked if obj_state.get(r.item.id) == "current"]
        reranked = ranking.rank(cur_items, evals)
        if is_setup:
            current_setups_ranked = reranked
        for cr in reranked:
            current_ranking[cr.item.id] = {
                "current_rank": cr.rank, "current_tied": cr.tied,
                "current_factors": {f.name: f.value for f in cr.factors},
                "current_pairwise_reason": cr.lost_to_prev or "TOP — no current competitor outranks it"}
    life["current_ranking"] = current_ranking
    # recommendation derives EXCLUSIVELY from the current-ranked setups (so its rank/RR/entry/stop/
    # target are the CURRENT winner's, not stale global metadata).
    recommendation = setup_mod.recommend(current_setups_ranked)

    return MarketState(
        tf=tf, n_bars=len(bars), fractal_width=w,
        classified=classified, structural=structural, pools=pools,
        active_erl=active_erl, ranges=ranges, ranked_sweeps=ranked_sweeps,
        ranked_displacements=ranked_disp, ranked_mss=ranked_mss, ranked_fvgs=ranked_fvg,
        ranked_setups=ranked_setups, recommendation=recommendation,
        tier_counts=significance.counts(classified),
        liq_counts={"active": len(active_erl), "swept": len(swing_liquidity.swept(pools))},
        lifecycle=life,
    )
