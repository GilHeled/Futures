"""Candidate lifecycle / context separation (EXPERIMENTAL; nothing frozen).

Within a lookback window the engine legitimately DETECTS many sweeps/MSS/FVGs/setups — but most
belong to OLD, already-resolved theses. Comparing an old sweep from days ago against today's live
manipulation just because both sit in the window is a context error. So each object is assigned a
lifecycle STATE and only CURRENT COMPETITORS may rank for the current decision:

  * historical      — thesis structurally completed / invalidated / SUPERSEDED (kept for audit,
                      NEVER ranks): its FVG is mitigated, or a later OPPOSING MSS superseded it.
  * active          — thesis still structurally live (not mitigated, not superseded) but not
                      currently actionable (e.g. RR not met).
  * current         — an ACTIONABLE setup on the CURRENT (latest, un-opposed) structural thesis;
                      only these compete for the recommendation.

Supersession is STRUCTURAL and CAUSAL: a setup is superseded when an opposing-direction MSS formed
AFTER its MSS (a newer structural shift the other way) — not a fixed "last N bars" filter. Object
tiers propagate along the dependency chain (a current setup's sweep/MSS/FVG/ERL/swing are current).
This module imports only `ids` (no pipeline/reasoning) to avoid import cycles.
"""
from __future__ import annotations

from ict_live.market.calendar import Calendar
from ict_live.structure import ids

_CAL = Calendar()


def _mss_time(m):
    return m.confirm_index if m.confirm_index is not None else m.broken_index


def classify(*, ranked_setups, ranked_mss, ranked_sweeps, ranked_displacements,
             ranked_fvgs, pools, structural, cursor_time=None) -> dict:
    """`cursor_time` (ET) enables the intraday session-day expiry: a thesis whose manipulation
    happened on an EARLIER CME session-day than the cursor is no longer current (its day's AMD has
    completed). This is STRUCTURAL (calendar session boundary), not a blind N-bar cutoff."""
    mss_by_id = {r.item.id: r.item for r in ranked_mss}
    sweep_by_id = {r.item.id: r.item for r in ranked_sweeps}
    all_mss = [r.item for r in ranked_mss]
    current_direction = max(all_mss, key=_mss_time).direction if all_mss else None
    cursor_day = _CAL.session_day(cursor_time) if cursor_time is not None else None

    setup_state, setup_reason, current_ids, active_ids = {}, {}, set(), set()
    for r in ranked_setups:
        s = r.item
        mss_id = next((d for d in s.depends_on if d.startswith("MSS")), None)
        swp_id = next((d for d in s.depends_on if d.startswith("SWP")), None)
        m, swp = mss_by_id.get(mss_id), sweep_by_id.get(swp_id)
        superseded = False
        if m is not None:
            mt = _mss_time(m)
            superseded = any(mm.direction != m.direction and _mss_time(mm) > mt for mm in all_mss)
        expired_session = False
        manip_day = None
        if cursor_day is not None and swp is not None:
            manip_day = _CAL.session_day(swp.time)
            expired_session = manip_day is not None and manip_day != cursor_day
        if "mitigated" in (s.reject_reason or ""):
            st, why = "historical", "entry FVG mitigated — thesis invalidated"
        elif m is None:
            st, why = "historical", "no live structure"
        elif superseded:
            st, why = "historical", "superseded by a later opposing MSS"
        elif expired_session:
            st, why = "historical", (f"intraday thesis expired — manipulation on session-day "
                                     f"{manip_day} precedes cursor session-day {cursor_day}")
        elif s.actionable:
            st, why = "current", "actionable on the current session-day thesis; not superseded"
            current_ids.add(s.id)
        else:
            st, why = "active", f"live thesis, not currently actionable ({s.reject_reason})"
            active_ids.add(s.id)
        setup_state[s.id] = st
        setup_reason[s.id] = why

    # dependency map for tier propagation along chains
    deps: dict[str, list] = {}
    for L in (ranked_setups, ranked_fvgs, ranked_mss, ranked_displacements, ranked_sweeps):
        for r in L:
            deps[r.item.id] = list(r.item.depends_on)
    for p in pools:
        deps[ids.pool_id(p)] = [f"SW{p.index}{'H' if p.kind == 'high' else 'L'}"]

    def closure(seed):
        seen, stack = set(), list(seed)
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(deps.get(x, []))
        return seen

    current_closure = closure(current_ids)
    active_closure = closure(active_ids) - current_closure

    all_ids = set(deps)
    for p in pools:
        all_ids.add(ids.pool_id(p))
    for s in structural:
        all_ids.add(ids.swing_id(s))
    object_state = {oid: ("current" if oid in current_closure else
                          "active" if oid in active_closure else "historical") for oid in all_ids}

    counts = {
        "current_setups": len(current_ids), "active_setups": len(active_ids),
        "historical_setups": sum(1 for v in setup_state.values() if v == "historical"),
        "current_objects": sum(1 for v in object_state.values() if v == "current"),
        "active_objects": sum(1 for v in object_state.values() if v == "active"),
        "historical_objects": sum(1 for v in object_state.values() if v == "historical"),
    }
    return {"current_direction": current_direction, "setup_state": setup_state,
            "setup_reason": setup_reason, "object_state": object_state,
            "current_setup_ids": current_ids, "active_setup_ids": active_ids, "counts": counts}


def current_competitor_setups(ranked_setups, life: dict) -> list:
    """The ranked setups that are CURRENT competitors (preserving rank order)."""
    cur = life.get("current_setup_ids", set())
    return [r for r in ranked_setups if r.item.id in cur]
