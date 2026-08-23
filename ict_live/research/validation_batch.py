"""Batch-3 = a VALIDATION set for execution v1 (not training). Unlike Batch-2 (deliberately all
premium/discount conflicts → almost all PASS), this stratifies scenes across the v1 execution-score
range so agreement is measured across the whole TRADE/PASS boundary, not just one side.

Scans dev (LOCKED OOS >=2025 never touched), keeps one live-setup scene per contract-day, computes
the transparent v1 score q = 0.6·pd_location + 0.4·ce_distance, EXCLUDES any scene already labelled
(Batch-1/2), then samples evenly from q-strata [0,0.25) [0.25,0.39) [0.39,0.6) [0.6,1] with year/
contract spread. Output: queue_batch3.jsonl (feed to annotation_csv.export for the spreadsheet).

Run under the venv: .venv/bin/python -m ict_live.research.validation_batch
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import annotations as anno
from ict_live.engine import execution_quality as EQ
from ict_live.engine import pipeline
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod

OUT = "ict_live/research/datasets/queue_batch3.jsonl"
FIDELITY = "ict_live/research/datasets/human_fidelity.jsonl"
STRATA = [(0.0, 0.25), (0.25, 0.39), (0.39, 0.6), (0.6, 1.01)]
_TF_MIN_STOP = 2.0


def _labelled_ids() -> set:
    return {a["scene_id"] for a in anno.load_annotations(FIDELITY)}


def _q(ms) -> float | None:
    f = EQ.factors(ms)
    if f is None:
        return None
    w = EQ.V1_WEIGHTS
    return round(sum(w[k] * f[k] for k in EQ.FACTOR_NAMES) / sum(w.values()), 4)


def scan(symbol: str, *, signal_tf="1H", entry_tf="15m", dev_start="2019-05-01",
         dev_end="2025-01-01", stride=12, window=240) -> list[dict]:
    assert dev_end <= "2025-01-01", "dev_end must not enter the locked OOS"
    bars5 = data_mod.load_5m(symbol, start=dev_start, end=dev_end)
    segments = rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))
    out, seen = [], set()
    for si, seg in enumerate(segments):
        if si == 0:
            continue
        sig = data_mod.resample(seg.bars, signal_tf)
        ref_all = data_mod.resample(seg.bars, entry_tf)
        for k in range(window, len(sig), stride):
            cc = sig[k].close_time
            ms = pipeline.analyze(sig[max(0, k - window + 1):k + 1], signal_tf,
                                  refine_bars=[b for b in ref_all if b.close_time <= cc],
                                  min_stop=_TF_MIN_STOP)
            if ms.recommendation.decision not in ("LONG", "SHORT"):
                continue
            key = (seg.contract, sig[k].open_time.date().isoformat())
            if key in seen:
                continue
            seen.add(key)
            q = _q(ms)
            if q is None:
                continue
            out.append({"scene_id": f"{symbol}:{seg.contract}:{signal_tf}:{sig[k].open_time.isoformat()}",
                        "symbol": symbol, "contract": seg.contract, "signal_tf": signal_tf,
                        "entry_tf": entry_tf, "time": sig[k].open_time.isoformat(),
                        "q": q, "year": sig[k].open_time.year})
    return out


def generate(symbols=("MES", "MNQ"), *, target=80, out_path=OUT) -> dict:
    labelled = _labelled_ids()
    pool = []
    for s in symbols:
        rows = [r for r in scan(s) if r["scene_id"] not in labelled]
        print(f"  {s}: {len(rows)} fresh candidate scenes")
        pool += rows
    # even split across q-strata, with year spread inside each stratum
    per = max(1, target // len(STRATA))
    queue, used = [], set()
    for lo, hi in STRATA:
        cand = [r for r in pool if lo <= r["q"] < hi and r["scene_id"] not in used]
        cand.sort(key=lambda r: (r["year"], r["symbol"], r["q"]))
        # spread across years: round-robin by year
        by_year: dict = {}
        for r in cand:
            by_year.setdefault(r["year"], []).append(r)
        picked = []
        while len(picked) < per and any(by_year.values()):
            for y in sorted(by_year):
                if by_year[y]:
                    picked.append(by_year[y].pop(0))
                    if len(picked) >= per:
                        break
        for r in picked:
            used.add(r["scene_id"])
        queue += picked
    queue.sort(key=lambda r: r["time"])
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(json.dumps(r, default=str) for r in queue))
    import collections
    return {"out_path": out_path, "pool": len(pool), "queued": len(queue),
            "by_stratum": {f"[{lo},{hi})": sum(1 for r in queue if lo <= r["q"] < hi) for lo, hi in STRATA},
            "by_symbol": dict(collections.Counter(r["symbol"] for r in queue)),
            "by_year": dict(sorted(collections.Counter(r["year"] for r in queue).items()))}


if __name__ == "__main__":
    print(json.dumps(generate(), indent=1, default=str))
