"""Structured, LEAKAGE-SAFE feature records for the learning layer.

Every value here is derived solely from a `MarketState` produced by `pipeline.analyze(bars[:k+1])`
— i.e. only information knowable at the decision bar k. No future session extremes, no eventual
swings, no outcome. Outcome labels are attached SEPARATELY, later, by the labelling pass (they are
never features).

Two record builders for now (extensible): one per SETUP candidate (the trade idea + its whole
dependency chain), and one per SWEEP candidate (for the "which sweep is the true manipulation"
ranking task the ML layer will learn). Each record carries the candidate's rank + factor values +
the competition context (how many candidates, this one's rank) so ranking models can train on it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ict_live.engine.pipeline import MarketState
from ict_live.market import sessions as S


def _by_id(ranked):
    return {r.item.id: r for r in ranked}


def _ctx(ms: MarketState, bar) -> dict:
    """Identity/time + dealing-range location context common to every candidate. `bar` may be None
    (e.g. a shadow prediction with no cursor bar) — then time/session/location context is omitted."""
    if bar is None:
        dr = ms.ranges[0] if ms.ranges else None
        return {"bar_time": None, "bar_index": ms.n_bars - 1, "tf": ms.tf, "session": None,
                "active_windows": [], "day_of_week": None,
                "dr_source_tf": dr.source_tf if dr else None, "dr_high": dr.high if dr else None,
                "dr_low": dr.low if dr else None, "dr_ce": dr.ce if dr else None,
                "dr_location_norm": None, "dr_zone": None,
                "n_active_erl": len(ms.active_erl), "n_structural": len(ms.structural)}
    et = bar.open_time
    dr = ms.ranges[0] if ms.ranges else None
    price = bar.close
    loc = None
    zone = None
    if dr and dr.high > dr.low:
        loc = round((price - dr.low) / (dr.high - dr.low), 4)
        zone = dr.zone_of(price)
    return {
        "bar_time": et.isoformat(),
        "bar_index": ms.n_bars - 1,
        "tf": ms.tf,
        "session": S.killzone(et),
        "active_windows": S.active_windows(et),
        "day_of_week": et.weekday(),
        "dr_source_tf": dr.source_tf if dr else None,
        "dr_high": dr.high if dr else None,
        "dr_low": dr.low if dr else None,
        "dr_ce": dr.ce if dr else None,
        "dr_location_norm": loc,           # 0=range low, 1=range high
        "dr_zone": zone,                   # premium / discount / equilibrium
        "n_active_erl": len(ms.active_erl),
        "n_structural": len(ms.structural),
    }


def sweep_feature_record(ms: MarketState, r_sweep, bar, meta: dict) -> dict:
    s = r_sweep.item
    rec = {"type": "sweep_candidate", "symbol": meta.get("symbol"),
           "contract": meta.get("contract"), "id": s.id, **_ctx(ms, bar)}
    rec.update({
        "direction": s.direction,
        "pool_price": s.pool_price,
        "manip_extreme": s.extreme,
        "penetration": round(abs(s.extreme - s.pool_price), 4),
        "rank": r_sweep.rank,
        "tied": r_sweep.tied,
        "n_competing_sweeps": len(ms.ranked_sweeps),
        "factors": {f.name: f.value for f in r_sweep.factors},
        "lost_to_prev": r_sweep.lost_to_prev,
        "depends_on": list(s.depends_on),
    })
    return rec


def setup_feature_record(ms: MarketState, r_setup, bar, meta: dict) -> dict:
    s = r_setup.item
    sweeps = _by_id(ms.ranked_sweeps)
    disps = _by_id(ms.ranked_displacements)
    mss = _by_id(ms.ranked_mss)
    fvgs = _by_id(ms.ranked_fvgs)

    # trace the chain via depends_on: setup -> (fvg, mss, sweep, dr, target pool)
    dep = list(s.depends_on)
    fvg_id = next((d for d in dep if d.startswith("FVG")), None)
    mss_id = next((d for d in dep if d.startswith("MSS")), None)
    swp_id = next((d for d in dep if d.startswith("SWP")), None)
    rf, rm, rs = fvgs.get(fvg_id), mss.get(mss_id), sweeps.get(swp_id)
    # displacement sits between fvg and sweep in the chain (fvg depends on disp)
    rd = None
    if rf is not None:
        dfid = next((d for d in rf.item.depends_on if d.startswith("DISP")), None)
        rd = disps.get(dfid)

    # ranking features for ML come from the CURRENT-competitor ranking (recomputed over current
    # objects only), NOT the global/historical ranking — falls back to global if no lifecycle.
    cr = (ms.lifecycle or {}).get("current_ranking", {})

    def _crank(rwrap):
        if rwrap is None:
            return None
        return cr.get(rwrap.item.id, {}).get("current_rank", rwrap.rank)

    n_current = len((ms.lifecycle or {}).get("current_setup_ids", [])) or \
        sum(1 for r in ms.ranked_setups if r.item.actionable)

    rec = {"type": "setup_candidate", "symbol": meta.get("symbol"),
           "contract": meta.get("contract"), "id": s.id, **_ctx(ms, bar)}
    rec.update({
        "direction": s.direction,
        "structure_tf": getattr(s, "structure_tf", ""), "entry_tf": getattr(s, "entry_tf", ""),
        "entry": s.entry, "stop": s.stop, "target": s.target,
        "rr": s.rr, "risk": s.risk, "reward": s.reward,
        "actionable": s.actionable, "reject_reason": s.reject_reason,
        "rank": _crank(r_setup), "tied": cr.get(s.id, {}).get("current_tied", r_setup.tied),
        "n_competing_setups": n_current,          # CURRENT competitors, not the whole window
        "n_actionable_setups": sum(1 for r in ms.ranked_setups if r.item.actionable),
        "factors": cr.get(s.id, {}).get("current_factors") or {f.name: f.value for f in r_setup.factors},
        "lost_to_prev": cr.get(s.id, {}).get("current_pairwise_reason", r_setup.lost_to_prev),
        "global_rank": r_setup.rank,              # retained as audit metadata only
        "depends_on": dep,
        # chain features (leakage-safe — all as-of-k; ranks from the current-competitor set)
        "sweep_direction": rs.item.direction if rs else None,
        "sweep_rank": _crank(rs),
        "sweep_rejection": (rs.factors[2].value if rs else None),
        "displacement_net": rd.item.net if rd else None,
        "displacement_speed": (round(rd.item.net / max(rd.item.span, 1), 3) if rd else None),
        "displacement_exhausted": rd.item.exhausted if rd else None,
        "mss_state": rm.item.state if rm else None,
        "mss_acceptance": rm.item.acceptance if rm else None,
        "mss_rank": _crank(rm),
        "fvg_status": rf.item.status if rf else None,
        "fvg_size": (round(rf.item.top - rf.item.bottom, 4) if rf else None),
        "fvg_rank": _crank(rf),
    })
    return rec
