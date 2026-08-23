"""Engine-vs-human fidelity comparison (steps 2–4 of the fidelity workflow).

Given a recovered scene (symbol/date/tf) + a HUMAN label (direction and, when marked, the true
manipulation / MSS / FVG / dealing-range context), run the engine's reasoning graph and compare,
dimension by dimension. Every disagreement is auto-classified into the frozen categories:

    detector · context · ranking · mechanization · ambiguity · discretionary

so we can improve the deterministic engine one discrepancy at a time. Comparison records are stored
in the HUMAN-FIDELITY dataset (separate from market outcomes, linked by scene id). The manipulation
dimension doubles as the primary FIDELITY-RANKING training signal ("which of the engine's ranked
sweeps did the human pick?"). This module is pandas-free (engine + stdlib only).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ict_live.engine import reasoning
from ict_live.engine.pipeline import MarketState

CATEGORIES = ("detector", "context", "ranking", "mechanization", "ambiguity", "discretionary")


@dataclass
class SceneLabel:
    """A human/expert label for a scene. Any field may be None (partial labels are fine)."""
    scene_id: str
    symbol: str
    date: str
    tf: str
    human_decision: Optional[str] = None        # LONG / SHORT / NO_TRADE
    human_manip_direction: Optional[str] = None  # bearish / bullish
    human_manip_level: Optional[float] = None    # the price of the true manipulation pool
    human_mss_level: Optional[float] = None
    human_fvg_ce: Optional[float] = None
    human_dr_zone: Optional[str] = None          # premium / discount / equilibrium
    confidence: Optional[float] = None
    note: str = ""
    source: str = "recovered"                    # provenance of the label
    error_tags: list = field(default_factory=list)


def _nearest(ranked, direction, level, price_of):
    """Index into `ranked` of the candidate matching direction and nearest to `level` (or None)."""
    cands = [(i, r) for i, r in enumerate(ranked)
             if direction is None or getattr(r.item, "direction", None) == direction]
    if not cands or level is None:
        return None
    i, _ = min(cands, key=lambda ir: abs(price_of(ir[1].item) - level))
    # require the match to be reasonably close (0.25% of level) else treat as not-detected
    best = cands[min(range(len(cands)), key=lambda j: abs(price_of(cands[j][1].item) - level))][1]
    return i if abs(price_of(best.item) - level) <= max(abs(level) * 0.0025, 1e-9) else None


def _classify(kind: str, detected: bool, engine_rank: Optional[int],
              direction_conflict: bool) -> str:
    """Initial rule-based category for a disagreement (human review can override)."""
    if not detected:
        return "detector"                 # engine never produced what the human marked
    if direction_conflict:
        return "context"                  # engine read the opposite bias/context
    if engine_rank is not None and engine_rank > 1:
        return "ranking"                  # engine had it, but deprioritized it
    return "ambiguity"                    # detected + top-ranked yet still disagreed -> needs review


def compare(ms: MarketState, label: SceneLabel) -> dict:
    g = reasoning.build_graph(ms)
    rec = ms.recommendation
    dims = []

    # 1. direction / final recommendation
    if label.human_decision is not None:
        eng = rec.decision
        agree = (eng == label.human_decision) or (eng == "NO-TRADE" and label.human_decision == "NO_TRADE")
        cat = None
        if not agree:
            # if the engine took the opposite side, it's a context problem; if engine NO-TRADE
            # while human traded, likely mechanization (a gate rejected it) or detector
            if eng in ("LONG", "SHORT") and label.human_decision in ("LONG", "SHORT"):
                cat = "context"
            elif eng == "NO-TRADE":
                cat = "mechanization" if ms.ranked_setups else "detector"
            else:
                cat = "ambiguity"
        dims.append({"dimension": "direction", "engine": eng, "human": label.human_decision,
                     "agree": agree, "category": cat})

    # 2. manipulation / sweep  (also the fidelity-ranking signal)
    if label.human_manip_level is not None:
        idx = _nearest(ms.ranked_sweeps, label.human_manip_direction, label.human_manip_level,
                       lambda s: s.pool_price)
        detected = idx is not None
        eng_top = ms.ranked_sweeps[0].item.pool_price if ms.ranked_sweeps else None
        eng_rank = (idx + 1) if detected else None
        dir_conflict = (label.human_manip_direction is not None and ms.ranked_sweeps
                        and ms.ranked_sweeps[0].item.direction != label.human_manip_direction)
        agree = detected and eng_rank == 1
        dims.append({"dimension": "manipulation", "engine_top": eng_top,
                     "human_level": label.human_manip_level,
                     "human_chosen_engine_rank": eng_rank,        # <- ranking training signal
                     "n_candidates": len(ms.ranked_sweeps), "agree": agree,
                     "category": None if agree else _classify("manip", detected, eng_rank, dir_conflict)})

    # 3. MSS
    if label.human_mss_level is not None:
        idx = _nearest(ms.ranked_mss, None, label.human_mss_level, lambda m: m.broken_price)
        detected = idx is not None
        eng_rank = (idx + 1) if detected else None
        agree = detected and eng_rank == 1
        dims.append({"dimension": "mss", "human_level": label.human_mss_level,
                     "human_chosen_engine_rank": eng_rank, "n_candidates": len(ms.ranked_mss),
                     "agree": agree,
                     "category": None if agree else _classify("mss", detected, eng_rank, False)})

    # 4. FVG
    if label.human_fvg_ce is not None:
        idx = _nearest(ms.ranked_fvgs, None, label.human_fvg_ce, lambda f: f.ce)
        detected = idx is not None
        eng_rank = (idx + 1) if detected else None
        agree = detected and eng_rank == 1
        dims.append({"dimension": "fvg", "human_ce": label.human_fvg_ce,
                     "human_chosen_engine_rank": eng_rank, "n_candidates": len(ms.ranked_fvgs),
                     "agree": agree,
                     "category": None if agree else _classify("fvg", detected, eng_rank, False)})

    # 5. dealing-range context
    if label.human_dr_zone is not None:
        dr = ms.ranges[0] if ms.ranges else None
        eng_zone = dr.direction if dr else None       # engine exposes leg direction as context
        # compare the human's premium/discount read against where price sits in the engine range
        agree = None
        cat = None
        if dr is not None:
            # can't know 'current price' here without a bar; report engine range direction + zone label
            agree = (label.human_dr_zone is not None)   # placeholder: recorded for human review
        dims.append({"dimension": "dealing_range", "engine_direction": eng_zone,
                     "human_zone": label.human_dr_zone, "agree": agree,
                     "category": "context" if dr is None else None})

    disagreements = [d for d in dims if d.get("agree") is False]
    by_cat = {}
    for d in disagreements:
        by_cat[d["category"]] = by_cat.get(d["category"], 0) + 1
    return {"type": "fidelity_comparison", "scene_id": label.scene_id, "symbol": label.symbol,
            "date": label.date, "tf": label.tf, "source": label.source,
            "engine_decision": rec.decision, "human_decision": label.human_decision,
            "confidence": label.confidence, "note": label.note, "error_tags": list(label.error_tags),
            "dimensions": dims, "n_disagreements": len(disagreements), "by_category": by_cat}


def append_comparison(path: str, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
