"""Reasoning graph — inspect EVERY object the engine created and the full reasoning behind any
recommendation. Pure/derived from a MarketState; no I/O. Feeds the visual microscope + course
comparison (fidelity work).

For every object it exposes: id, kind, why (rationale), rank + factor values + why-it-lost
(lost_to_prev), state/transitions, timestamps, dependencies (parents) and children (reverse deps),
competing alternatives (its ranked siblings), and rejection reasons. `trace(graph, node_id)` walks
the full ancestor chain (recommendation → setup → FVG → MSS → displacement → sweep → ERL → swing).
"""
from __future__ import annotations

from ict_live.engine.pipeline import MarketState
from ict_live.structure import ids


def _node(id, kind, why="", *, time=None, index=None, rank=None, tied=None, factors=None,
          lost_to_prev="", state=None, actionable=None, reject_reason=None, depends_on=()):
    return {"id": id, "kind": kind, "why": why, "time": (time.isoformat() if time else None),
            "index": index, "rank": rank, "current_rank": None, "tied": tied, "factors": factors or {},
            "lost_to_prev": lost_to_prev, "state": state, "actionable": actionable,
            "reject_reason": reject_reason, "depends_on": list(depends_on), "children": []}


def build_graph(ms: MarketState) -> dict:
    """All engine objects as a linked graph. Returns {nodes:{id:node}, layers:{kind:[ids ranked]},
    recommendation:{...}}."""
    nodes: dict[str, dict] = {}

    def add(n):
        nodes[n["id"]] = n
        return n["id"]

    # structural swings (+ their causal children the ERL pools point back to)
    for cs in ms.classified:
        if cs.tier != "structural":
            continue
        s = cs.swing
        st = "/".join(t for t, on in (("dominant", cs.dominant), ("protected", cs.protected),
                                       ("broken", cs.broken)) if on) or "structural"
        add(_node(ids.swing_id(s), "swing", cs.reason, time=s.time, index=s.index, state=st))
    for p in ms.pools:
        add(_node(ids.pool_id(p), "erl", p.reason, time=p.time, index=p.index,
                  state=("swept" if p.swept else "active"),
                  depends_on=[f"SW{p.index}{'H' if p.kind == 'high' else 'L'}"]))
    for dr in ms.ranges:
        add(_node(ids.dr_id(dr), "dealing_range", dr.reason, state=dr.direction))

    layers = {}

    def add_ranked(kind, ranked, state_fn=None, reject_fn=None):
        ids_in_order = []
        for r in ranked:
            it = r.item
            add(_node(it.id, kind, getattr(it, "reason", ""),
                      time=getattr(it, "time", None), index=getattr(it, "bar_index", None),
                      rank=r.rank, tied=r.tied,
                      factors={f.name: f.value for f in r.factors}, lost_to_prev=r.lost_to_prev,
                      state=state_fn(it) if state_fn else None,
                      actionable=getattr(it, "actionable", None),
                      reject_reason=reject_fn(it) if reject_fn else None,
                      depends_on=list(it.depends_on)))
            ids_in_order.append(it.id)
        layers[kind] = ids_in_order

    add_ranked("manipulation", ms.ranked_sweeps, state_fn=lambda x: x.direction)
    add_ranked("displacement", ms.ranked_displacements,
               state_fn=lambda x: "exhausted" if x.exhausted else "in_progress")
    add_ranked("mss", ms.ranked_mss, state_fn=lambda x: x.state)
    add_ranked("fvg", ms.ranked_fvgs, state_fn=lambda x: x.status)
    add_ranked("setup", ms.ranked_setups, state_fn=lambda x: "actionable" if x.actionable else "rejected",
               reject_fn=lambda x: x.reject_reason)

    # reverse edges (children)
    for n in nodes.values():
        for dep in n["depends_on"]:
            if dep in nodes:
                nodes[dep]["children"].append(n["id"])

    # tag each node with its lifecycle state (historical / active / current)
    obj_state = (ms.lifecycle or {}).get("object_state", {})
    for nid, n in nodes.items():
        n["lifecycle"] = obj_state.get(nid, "historical" if obj_state else "current")
    # current_rank / current_pairwise_reason come from a ranking RECOMPUTED over current competitors
    # only (pipeline). `rank`/`lost_to_prev` remain the global/historical values (audit metadata).
    cur_rank = (ms.lifecycle or {}).get("current_ranking", {})
    for nid, n in nodes.items():
        cr = cur_rank.get(nid)
        if cr:
            n["current_rank"] = cr["current_rank"]
            n["current_tied"] = cr["current_tied"]
            n["current_pairwise_reason"] = cr["current_pairwise_reason"]
            n["current_factors"] = cr["current_factors"]

    rec = ms.recommendation
    recommendation = {"decision": rec.decision, "reason": rec.reason,
                      "setup_id": rec.setup.id if rec.setup else None,
                      "depends_on": list(rec.depends_on)}
    return {"nodes": nodes, "layers": layers, "recommendation": recommendation,
            "lifecycle": ms.lifecycle or {}}


def trace(graph: dict, node_id: str) -> list[str]:
    """Full ancestor chain of `node_id` (itself + all transitive dependencies), nearest first."""
    nodes = graph["nodes"]
    seen, order, stack = set(), [], [node_id]
    while stack:
        nid = stack.pop(0)
        if nid in seen or nid not in nodes:
            continue
        seen.add(nid)
        order.append(nid)
        stack.extend(nodes[nid]["depends_on"])
    return order


def competitors(graph: dict, kind: str) -> list[dict]:
    """The ranked sibling set for a layer (all candidates, ranked, with factors + why-lost)."""
    return [graph["nodes"][i] for i in graph["layers"].get(kind, [])]
