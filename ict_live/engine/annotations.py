"""Human-FIDELITY annotation schema — "would the discretionary methodology take this?"

This is a SEPARATE dataset from market outcomes (engine/outcomes.py). The two are linked ONLY by
candidate/setup id and must never be conflated: a faithful setup can lose; an unfaithful one can
win. Fidelity labels are NEVER inferred from P&L — they record the human's methodological judgment.

An annotation captures a decision (LONG/SHORT/NO_TRADE and/or ACCEPT/REJECT), optional structured
error tags saying WHERE the engine's read diverged from the human's, a free-text note, and a
confidence. Stored append-only as JSONL; a candidate may accrue multiple annotations over time.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# a directional/accept call — both dimensions allowed (e.g. REJECT + NO_TRADE, or ACCEPT + SHORT)
DECISIONS = frozenset({"LONG", "SHORT", "NO_TRADE", "ACCEPT", "REJECT"})

# structured "what was wrong" tags (why the human disagrees with the engine's read).
# NOTE (Batch-2 focus): the STRUCTURAL error tags (wrong_manipulation/wrong_sweep/wrong_mss/
# wrong_fvg/wrong_dealing_range_context) are treated as VALIDATED after Batch-1 (0 structural
# disagreements) and are no longer the collection focus. Batch-2 targets the EXECUTION decision:
# would_execute + location_quality + reason_for_pass (below).
ERROR_TAGS = frozenset({
    "wrong_manipulation", "wrong_sweep", "wrong_mss", "wrong_fvg",
    "wrong_dealing_range_context", "bad_location", "insufficient_confirmation", "other",
})

# why the human would PASS on a structurally-valid setup (execution-selectivity labels, Batch-2).
# These describe execution quality only; they NEVER change the structural direction.
# one reason per execution factor (symmetric with execution_quality.FACTOR_NAMES) + other:
#   premium_discount ↔ pd_location · too_far_from_ce ↔ ce_distance · rr_misleading ↔ rr_realism
#   insufficient_confirmation ↔ confirmation · fvg_location ↔ fvg_location
PASS_REASONS = frozenset({
    "premium_discount",          # entry on the wrong premium/discount side
    "too_far_from_ce",           # entry pushed too deep into the imbalance
    "rr_misleading",             # high RR inflated by a distant/implausible liquidity target
    "insufficient_confirmation", # MSS / displacement / sweep rejection too weak
    "fvg_location",              # low-quality entry FVG (touched/mitigated or bad size)
    "other",
})


def make_annotation(*, candidate_id: str, decisions: list[str], annotator: str,
                    error_tags: Optional[list[str]] = None, note: str = "",
                    confidence: Optional[float] = None,
                    candidate_time: Optional[str] = None,
                    would_execute: Optional[bool] = None,
                    location_quality: Optional[int] = None,
                    reason_for_pass: Optional[list[str]] = None) -> dict:
    """Build + validate one fidelity annotation. Raises ValueError on an invalid schema so bad
    labels never enter the dataset."""
    decisions = list(decisions or [])
    error_tags = list(error_tags or [])
    reason_for_pass = list(reason_for_pass or [])
    bad_d = set(decisions) - DECISIONS
    if not decisions or bad_d:
        raise ValueError(f"decisions must be a non-empty subset of {sorted(DECISIONS)}; got {decisions}")
    bad_t = set(error_tags) - ERROR_TAGS
    if bad_t:
        raise ValueError(f"unknown error_tags {sorted(bad_t)}; allowed {sorted(ERROR_TAGS)}")
    bad_r = set(reason_for_pass) - PASS_REASONS
    if bad_r:
        raise ValueError(f"unknown reason_for_pass {sorted(bad_r)}; allowed {sorted(PASS_REASONS)}")
    if "other" in error_tags and not note.strip():
        raise ValueError("error_tag 'other' requires a free-text note")
    if "other" in reason_for_pass and not note.strip():
        raise ValueError("reason_for_pass 'other' requires a free-text note")
    if confidence is not None and not (0.0 <= float(confidence) <= 1.0):
        raise ValueError("confidence must be in [0,1]")
    if location_quality is not None and int(location_quality) not in (1, 2, 3, 4, 5):
        raise ValueError("location_quality must be an integer 1..5")
    if would_execute is not None and not isinstance(would_execute, bool):
        raise ValueError("would_execute must be True/False/None")
    if not annotator.strip():
        raise ValueError("annotator is required")
    return {
        "type": "human_fidelity",
        "candidate_id": candidate_id,
        "candidate_time": candidate_time,
        "annotator": annotator,
        "decisions": decisions,
        "error_tags": error_tags,
        "note": note,
        "confidence": (float(confidence) if confidence is not None else None),
        # execution-decision fields (separate from structural correctness)
        "would_execute": would_execute,
        "location_quality": (int(location_quality) if location_quality is not None else None),
        "reason_for_pass": reason_for_pass,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def append_annotation(path: str, annotation: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        fh.write(json.dumps(annotation) + "\n")


def load_annotations(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def latest_by_candidate(path: str) -> dict[str, dict]:
    """Most-recent annotation per candidate_id (a candidate may be re-annotated over time)."""
    out: dict[str, dict] = {}
    for a in load_annotations(path):
        out[a["candidate_id"]] = a          # file is append-only chronological → last wins
    return out
