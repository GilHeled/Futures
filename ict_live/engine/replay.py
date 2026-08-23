"""Causal replay / dataset generator — turn the ICT engine into a data-generating machine.

Walks a bar series causally and, at each completed bar k, runs the EXACT same
`pipeline.analyze(bars[:k+1])` the live/microscope path uses, then records the COMPLETE market
state: the decision (LONG/SHORT/NO-TRADE) AND every candidate — taken, rejected, competing — as a
structured, leakage-safe feature record. Quiet periods (NO-TRADE, zero candidates) are recorded
too; the dataset is not just trades.

Leakage-safe BY CONSTRUCTION: a record at bar k is a pure function of bars[:k+1] (prefix-stable),
so nothing from the future can enter a feature. Outcome labels are attached by a SEPARATE later
pass and are never features.

Output: append-only JSONL, one record per line, `type` ∈ {decision, setup_candidate,
sweep_candidate}. Records are JSON-safe dicts (see engine/features.py).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Optional

from ict_live.engine import features, pipeline
from ict_live.market.bar import Bar


def decision_record(ms, bar, meta: dict) -> dict:
    rec = ms.recommendation
    return {
        "type": "decision",
        "symbol": meta.get("symbol"), "contract": meta.get("contract"), "tf": ms.tf,
        "bar_time": bar.open_time.isoformat(), "bar_index": ms.n_bars - 1,
        "decision": rec.decision, "setup_id": rec.setup.id if rec.setup else None,
        "reason": rec.reason, "depends_on": list(rec.depends_on),
        "n_setups": len(ms.ranked_setups),
        "n_actionable": sum(1 for r in ms.ranked_setups if r.item.actionable),
        "n_sweeps": len(ms.ranked_sweeps), "n_mss": len(ms.ranked_mss),
        "n_fvgs": len(ms.ranked_fvgs), "n_active_erl": len(ms.active_erl),
    }


def generate(bars: list[Bar], tf: str, meta: dict, *, warmup: int = 20, stride: int = 1,
             out_path: Optional[str] = None, emit_sweeps: bool = True,
             emit_setups: bool = True) -> dict:
    """Generate the causal dataset over `bars`. Returns a summary; writes JSONL if `out_path`."""
    fh = None
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fh = open(out_path, "w")
    counts = Counter()
    decisions = Counter()
    try:
        for k in range(max(warmup, 1) - 1, len(bars), stride):
            ms = pipeline.analyze(bars[:k + 1], tf)
            bar = bars[k]
            recs = [decision_record(ms, bar, meta)]
            decisions[recs[0]["decision"]] += 1
            if emit_setups:
                recs += [features.setup_feature_record(ms, r, bar, meta) for r in ms.ranked_setups]
            if emit_sweeps:
                recs += [features.sweep_feature_record(ms, r, bar, meta) for r in ms.ranked_sweeps]
            for rec in recs:
                counts[rec["type"]] += 1
                if fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
    finally:
        if fh:
            fh.close()
    return {"bars": len(bars), "decision_points": sum(decisions.values()),
            "decisions": dict(decisions), "record_counts": dict(counts), "out_path": out_path}
