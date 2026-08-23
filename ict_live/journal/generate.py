"""Populate trade records from historical replay — the fastest way to a baseline expectancy read.

Runs the FROZEN engine over dev bars (LOCKED OOS >=2025 never touched), records each distinct current
recommendation once (both v1 TRADE and v1 PASS, so stats.py can compare them), and attaches the
market outcome via engine.outcomes on the signal-TF series (causal: outcome scans only bars at/after
the decision). One record per (contract, setup id). Deterministic; no optimization.

Run under the venv: .venv/bin/python -m ict_live.journal.generate
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import outcomes as OUT
from ict_live.engine import pipeline
from ict_live.journal import record as REC
from ict_live.market.calendar import Calendar
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod

OUT_PATH = "ict_live/research/datasets/trade_records.jsonl"
_TF_MIN_STOP = 2.0


def generate(symbols=("MES", "MNQ"), *, signal_tf="1H", entry_tf="15m",
             dev_start="2019-05-01", dev_end="2025-01-01", stride=6, window=240,
             horizon_bars=OUT.DEFAULT_HORIZON_BARS, out_path=OUT_PATH) -> dict:
    assert dev_end <= "2025-01-01", "dev_end must not enter the locked OOS"
    cal = Calendar()
    records = []
    for symbol in symbols:
        bars5 = data_mod.load_5m(symbol, start=dev_start, end=dev_end)
        segments = rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))
        seen_setups = set()
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
                win = ms.recommendation.setup
                if win is None:
                    continue
                key = (seg.contract, win.id)
                if key in seen_setups:
                    continue
                seen_setups.add(key)
                direction = win.direction                       # "long"/"short"
                outcome = OUT.label_setup(id=win.id, symbol=symbol, tf=signal_tf, direction=direction,
                                          entry=win.entry, stop=win.stop, target=win.target,
                                          decision_index=k, bars=sig, horizon_bars=horizon_bars,
                                          calendar=cal)
                scene_id = f"{symbol}:{seg.contract}:{signal_tf}:{sig[k].open_time.isoformat()}"
                tr = REC.build(ms, scene_id=scene_id, symbol=symbol,
                               timestamp=sig[k].open_time.isoformat(), outcome=outcome)
                if tr is not None:
                    records.append(tr)
        print(f"  {symbol}: {sum(1 for r in records if r.symbol == symbol)} records")
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("\n".join(json.dumps(r.to_dict(), default=str) for r in records))
    return {"symbols": list(symbols), "records": len(records), "out_path": out_path}


if __name__ == "__main__":
    from ict_live.journal import stats as STATS
    rep = generate()
    rows = [json.loads(l) for l in Path(OUT_PATH).read_text().splitlines() if l.strip()]
    s = STATS.summarize(rows)
    Path("ict_live/research/RESULT_baseline_expectancy.md").write_text(
        STATS.render_md(s, title="Baseline expectancy — engine + execution v1 (dev, historical replay)"))
    print(json.dumps({**rep, **{"overall": s["overall"], "by_execution": s["by_execution"],
                                "v1_filter_edge_R": s["v1_filter_edge_R"]}}, indent=1, default=str))
