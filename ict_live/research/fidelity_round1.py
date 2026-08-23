"""Fidelity comparison on the recovered ROUND-1 human-reference scenes (MES 1H).

These 6 scenes are the only pre-existing blinded-audit examples whose dates were revealed
(mnq_system/strategies/ict_amd/FIDELITY_RESULT.md); the expert (user) labels are encoded below.
For each, run the current engine at the s5 (NY-AM ~10:00 ET) causal cursor, compare engine vs
human via fidelity_compare, classify disagreements, and store to the human-fidelity dataset.
"""
from __future__ import annotations

import json
from datetime import time

from ict_live.engine import pipeline
from ict_live.research import data as data_mod
from ict_live.research import fidelity_compare as fc
from ict_live.research import rolls as rolls_mod

FIDELITY_PATH = "ict_live/research/datasets/fidelity_comparisons.jsonl"

# recovered round-1 scenes: (id, date, human_decision, manip_direction expected, note)
SCENES = [
    ("B1", "2021-02-09", "NO_TRADE", None, "bias up; no setup all 5 snapshots"),
    ("B2", "2021-07-05", "NO_TRADE", "bullish", "human saw sell-side sweep + bullish displacement, did not trade"),
    ("B3", "2022-04-19", "LONG", "bullish", "conditional long; sell-side sweep + bullish displacement (s4/s5)"),
    ("B4", "2024-04-09", "SHORT", "bearish", "textbook short; buy-side sweep -> bearish MSS, daily bias down"),
    ("B5", "2024-10-02", "NO_TRADE", None, "bias down; no trade"),
    ("B6", "2024-11-28", "NO_TRADE", None, "bias up; no trade"),
]
_CURSOR = time(10, 0)   # s5 ~ NY-AM 10:00 ET


def _state_at(symbol: str, date: str, tf: str = "1H", window: int = 240):
    year = int(date[:4])
    bars5 = data_mod.load_5m(symbol, start=f"{year-1}-09-01", end=f"{date}T23:59:00")
    segs = rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))
    for seg in reversed(segs):                 # the scene sits in the latest segment covering it
        sig = data_mod.resample(seg.bars, tf)
        idx = [k for k, b in enumerate(sig)
               if b.open_time.date().isoformat() == date and b.open_time.timetz().replace(tzinfo=None) <= _CURSOR]
        if idx:
            k = idx[-1]
            lo = max(0, k - window + 1)
            return pipeline.analyze(sig[lo:k + 1], tf), seg.contract, sig[k].open_time.isoformat()
    return None, None, None


def run() -> dict:
    rows, summary = [], {"scenes": 0, "direction_agree": 0, "by_category": {}}
    for sid, date, decision, manip_dir, note in SCENES:
        ms, contract, ctime = _state_at("MES", date)
        if ms is None:
            print(f"  {sid} {date}: scene not found in cache")
            continue
        label = fc.SceneLabel(scene_id=f"MES:{sid}:{date}", symbol="MES", date=date, tf="1H",
                              human_decision=decision, human_manip_direction=manip_dir,
                              note=note, source="round1_blinded_audit", confidence=0.9)
        rec = fc.compare(ms, label)
        rec["contract"], rec["cursor_time"] = contract, ctime
        fc.append_comparison(FIDELITY_PATH, rec)
        rows.append(rec)
        summary["scenes"] += 1
        d0 = rec["dimensions"][0]
        summary["direction_agree"] += 1 if d0["agree"] else 0
        for c, n in rec["by_category"].items():
            summary["by_category"][c] = summary["by_category"].get(c, 0) + n
        print(f"  {sid} {date} {contract} @ {ctime[11:16]}: engine={rec['engine_decision']:<9} "
              f"human={decision:<9} {'AGREE' if d0['agree'] else 'DISAGREE ('+str(d0['category'])+')'} "
              f"| sweeps={next((d['n_candidates'] for d in rec['dimensions'] if d['dimension']=='manipulation'),'-')}")
    summary["fidelity_path"] = FIDELITY_PATH
    return summary


if __name__ == "__main__":
    print("Round-1 fidelity comparison (MES 1H, s5≈10:00 ET):")
    print(json.dumps(run(), indent=1))
