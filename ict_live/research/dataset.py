"""Segmented, causal ML-dataset builder: raw 5m → segment(rolls) → resample → engine → features
→ outcomes, one row per distinct candidate. Research/offline only.

Frozen data decisions (enforced here):
  * raw Databento 5m, NO back-adjustment;
  * explicit roll boundaries; state reset every segment (a fresh engine per segment);
  * no feature OR outcome window may cross a roll (post-roll warmup + horizon-fits-in-segment);
  * no future-aware roll metadata (bars_until_roll) enters features.

Causal generation: at each signal bar k we run `pipeline.analyze` on a BOUNDED rolling lookback
window ending at k (the engine's effective history — consistent with how it was validated on
~hundreds of bars, and what keeps this O(n·W) instead of O(n²)). A distinct setup is emitted ONCE,
the first bar it appears (deduped by contract/tf/direction/entry/stop/target). Outcomes are labelled
on the segment's finer 5m bars (better-than-signal intrabar ordering). Features and outcome labels
are kept in SEPARATE sub-objects, linked by a stable id.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from ict_live.engine import features, outcomes, pipeline
from ict_live.market.bar import Bar
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod

# 5m bars per signal bar (for the signal→5m outcome index mapping / horizon in 5m units)
_TF_5M = {"15m": 3, "1H": 12, "4H": 48}


def _stable_id(symbol, contract, tf, s) -> str:
    return f"{symbol}:{contract}:{tf}:{s.direction}:{s.entry:g}:{s.stop:g}:{s.target if s.target is not None else 'na'}"


def _first_5m_at_or_after(seg_bars: list[Bar], close_time: datetime, hint: int = 0) -> Optional[int]:
    for j in range(hint, len(seg_bars)):
        if seg_bars[j].open_time >= close_time:
            return j
    return None


def _target_labels(course: dict, fixed: dict) -> dict:
    """Supervised targets DERIVED from outcomes (labels, never features). Kept minimal + explicit."""
    res = course.get("result")
    tbs = 1 if res == "TARGET" else (0 if res == "STOP" else None)   # target-before-stop (clean only)
    return {
        "target_before_stop": tbs,                 # None for AMBIGUOUS/HORIZON/NO_FILL (excluded by ML)
        "r2_before_stop": (1 if fixed.get("r2_hit") is True else
                           (0 if fixed.get("r2_hit") is False else None)),
        "realized_R": course.get("realized_R"),
        "course_result": res,
    }


def build_dataset(symbol: str, *, signal_tf: str = "1H", source: str = "databento",
                  dev_start: str = "2019-05-01", dev_end: str = "2025-01-01",
                  train_end: str = "2024-01-01", window: int = 240, warmup: int = 60,
                  horizon_5m: int = 288, roll_exclusion_5m: int = 24,
                  out_dir: str = "ict_live/research/datasets") -> dict:
    """Build the dev (TRAIN+VAL) candidate dataset for `symbol`/`signal_tf`. The LOCKED OOS period
    (>= dev_end) is never generated here. Returns a stats report; writes candidates + decisions JSONL."""
    assert dev_end <= "2025-01-01", "dev_end must not enter the locked OOS (>=2025-01-01)"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    cand_path = Path(out_dir) / f"candidates_{symbol}_{signal_tf}.jsonl"
    dec_path = Path(out_dir) / f"decisions_{symbol}_{signal_tf}.jsonl"
    train_end_dt = datetime.fromisoformat(train_end)

    bars5 = data_mod.load_5m(symbol, source=source, start=dev_start, end=dev_end)
    boundaries = rolls_mod.detect_rolls(bars5, root=symbol)
    segments = rolls_mod.segment(bars5, boundaries)
    n5 = _TF_5M[signal_tf]

    stats = {"symbol": symbol, "signal_tf": signal_tf, "window": window, "horizon_5m": horizon_5m,
             "n_segments": len(segments), "n_candidates": 0, "n_decisions": 0,
             "by_year": Counter(), "by_contract": Counter(), "by_split": Counter(),
             "target_class": Counter(), "purged_roll": 0, "purged_split": 0, "dup_skipped": 0,
             "missing_feature_counts": Counter(), "quiet_decisions": Counter()}

    cf = open(cand_path, "w")
    df = open(dec_path, "w")
    try:
        for seg in segments:
            sig = data_mod.resample(seg.bars, signal_tf)
            seen: set[str] = set()
            hint5 = 0
            for k in range(warmup, len(sig)):
                lo = max(0, k - window + 1)
                ms = pipeline.analyze(sig[lo:k + 1], signal_tf)
                sig_bar = sig[k]
                # quiet/decision record (research; not the ML candidate set)
                df.write(json.dumps({"type": "decision", "symbol": symbol, "contract": seg.contract,
                                     "signal_tf": signal_tf, "decision_time": sig_bar.open_time.isoformat(),
                                     "decision": ms.recommendation.decision,
                                     "n_setups": len(ms.ranked_setups),
                                     "n_actionable": sum(1 for r in ms.ranked_setups if r.item.actionable)},
                                    default=str) + "\n")
                stats["n_decisions"] += 1
                stats["quiet_decisions"][ms.recommendation.decision] += 1

                for r in ms.ranked_setups:
                    s = r.item
                    sid = _stable_id(symbol, seg.contract, signal_tf, s)
                    if sid in seen:
                        stats["dup_skipped"] += 1
                        continue
                    seen.add(sid)
                    # map decision to the segment's 5m timeline
                    dec5 = _first_5m_at_or_after(seg.bars, sig_bar.close_time, hint5)
                    if dec5 is None:
                        continue
                    hint5 = dec5
                    # roll exclusions: post-roll warmup + outcome horizon must fit inside the segment
                    if dec5 < roll_exclusion_5m or dec5 + horizon_5m > len(seg.bars) - 1:
                        stats["purged_roll"] += 1
                        continue
                    feat = features.setup_feature_record(ms, r, sig_bar, {"symbol": symbol,
                                                                          "contract": seg.contract})
                    # causal roll metadata only (bars_since_roll); NEVER bars_until_roll
                    feat["bars_since_roll"] = seg.start_index + dec5   # position within contract seg (5m)
                    feat["contract"] = seg.contract
                    oc = outcomes.label_setup(id=sid, symbol=symbol, tf=signal_tf, direction=s.direction,
                                              entry=s.entry, stop=s.stop, target=s.target,
                                              decision_index=dec5, bars=seg.bars, horizon_bars=horizon_5m)
                    labels = _target_labels(oc.get("course_execution", {}), oc.get("fixed_r", {}))
                    # split by decision time; PURGE candidates whose outcome window straddles the boundary
                    dtime = sig_bar.open_time.replace(tzinfo=None)
                    final_t = oc.get("course_execution", {}).get("final_time")
                    final_dt = datetime.fromisoformat(final_t).replace(tzinfo=None) if final_t else dtime
                    if dtime < train_end_dt <= final_dt:
                        stats["purged_split"] += 1
                        continue
                    split = "train" if dtime < train_end_dt else "val"

                    row = {"id": sid, "type": "setup_candidate", "symbol": symbol,
                           "contract": seg.contract, "signal_tf": signal_tf,
                           "decision_time": sig_bar.open_time.isoformat(), "split": split,
                           "engine_decision": ms.recommendation.decision,
                           "rank": r.rank, "n_competing_setups": len(ms.ranked_setups),
                           "actionable": s.actionable, "direction": s.direction,
                           "entry": s.entry, "stop": s.stop, "target": s.target, "rr": s.rr,
                           "features": feat, "outcome": oc, "labels": labels}
                    cf.write(json.dumps(row, default=str) + "\n")
                    stats["n_candidates"] += 1
                    stats["by_year"][sig_bar.open_time.year] += 1
                    stats["by_contract"][seg.contract] += 1
                    stats["by_split"][split] += 1
                    stats["target_class"][labels["target_before_stop"]] += 1
                    for kf, vf in feat.items():
                        if vf is None:
                            stats["missing_feature_counts"][kf] += 1
    finally:
        cf.close()
        df.close()

    for key in ("by_year", "by_contract", "by_split", "target_class", "quiet_decisions",
                "missing_feature_counts"):
        stats[key] = dict(stats[key])
    stats["candidates_path"] = str(cand_path)
    stats["decisions_path"] = str(dec_path)
    return stats
