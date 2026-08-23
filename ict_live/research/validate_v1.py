"""Batch-3 VALIDATION GATE for execution v1 — measure only, never retune.

Reproduces the current winning setup for each Batch-3 label, runs the SHIPPED execution v1
(engine/execution_quality.assess), and compares its TRADE/PASS to the human decision. Reports the
three pre-registered freeze metrics and lists the failure cases. Does NOT fit or change anything.

Pre-registered freeze criterion (all three): balanced accuracy >= 0.80, false-PASS <= 0.15,
PASS-recall >= 0.80.

Run under the venv: .venv/bin/python -m ict_live.research.validate_v1
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import annotations as anno
from ict_live.engine import execution_quality as EQ
from ict_live.research import annotation_app as APP

FIDELITY = "ict_live/research/datasets/human_fidelity.jsonl"
RESULT_MD = "ict_live/research/RESULT_v1_validation.md"
ROWS_CACHE = "ict_live/research/datasets/validate_v1_rows.json"
ROUND = "validation_batch3"
CRIT = {"balanced_accuracy": 0.80, "false_pass": 0.15, "pass_recall": 0.80}


def _human_dir(rec) -> str:
    for d in rec["decisions"]:
        if d in ("LONG", "SHORT", "NO_TRADE"):
            return d
    return "NO_TRADE"


def collect(round_name=ROUND, *, use_cache=True) -> list[dict]:
    if use_cache and Path(ROWS_CACHE).exists():
        return json.loads(Path(ROWS_CACHE).read_text())
    latest = {}
    for a in anno.load_annotations(FIDELITY):
        if a.get("provenance", {}).get("round") == round_name:
            latest[a["scene_id"]] = a
    rows, skipped = [], []
    for i, (sid, a) in enumerate(latest.items(), 1):
        try:
            ms, _ = APP.build_state({"symbol": a["symbol"], "contract": a["contract"],
                                     "signal_tf": a["signal_tf"], "time": a["candidate_time"]})
        except SystemExit:
            ms = None
        if ms is None or EQ.factors(ms) is None:
            skipped.append(sid)
            continue
        ea = EQ.assess(ms)
        rows.append({"scene_id": sid, "symbol": a["symbol"],
                     "human_dir": _human_dir(a), "would_execute": a.get("would_execute"),
                     "reason_for_pass": a.get("reason_for_pass", []),
                     "v1_execution": ea.execution, "v1_pred": 1 if ea.execution == "TRADE" else 0,
                     "q": ea.confidence, "weakest": ea.weakest_factor,
                     "engine_direction": ea.structural,
                     "pd_location": ea.factors["pd_location"], "ce_distance": ea.factors["ce_distance"]})
        if i % 20 == 0:
            print(f"  reproduced {i}/{len(latest)}")
    if skipped:
        print(f"  skipped {len(skipped)} (no live setup at reproduction): {skipped}")
    Path(ROWS_CACHE).write_text(json.dumps(rows, indent=1))
    return rows


def score(all_rows) -> dict:
    # EXECUTION label = would_execute (the actual TRADE/PASS decision). Structural agreement
    # (direction) is reported separately. Rows without would_execute are excluded from the gate.
    struct_ok = sum(1 for r in all_rows if r["human_dir"] == r["engine_direction"])
    rows = [r for r in all_rows if r["would_execute"] is not None]
    for r in rows:
        r["human_exec"] = 1 if r["would_execute"] else 0
    tp = sum(1 for r in rows if r["v1_pred"] == 1 and r["human_exec"] == 1)
    tn = sum(1 for r in rows if r["v1_pred"] == 0 and r["human_exec"] == 0)
    fp = sum(1 for r in rows if r["v1_pred"] == 1 and r["human_exec"] == 0)
    fn = sum(1 for r in rows if r["v1_pred"] == 0 and r["human_exec"] == 1)
    trade_tot, pass_tot = tp + fn, tn + fp
    pass_recall = tn / pass_tot if pass_tot else None
    trade_kept = tp / trade_tot if trade_tot else None
    false_pass = fn / trade_tot if trade_tot else None          # good trades wrongly PASSed
    over_pass = fp / pass_tot if pass_tot else None
    bal_acc = (pass_recall + trade_kept) / 2 if (pass_recall is not None and trade_kept is not None) else None
    agree = (tp + tn) / len(rows) if rows else None
    passed = (bal_acc is not None and bal_acc >= CRIT["balanced_accuracy"]
              and false_pass is not None and false_pass <= CRIT["false_pass"]
              and pass_recall is not None and pass_recall >= CRIT["pass_recall"])
    # calibration by q-bin
    calib = []
    for lo, hi in [(0, .25), (.25, .39), (.39, .6), (.6, 1.01)]:
        b = [r for r in rows if lo <= r["q"] < hi]
        if b:
            calib.append({"bin": f"[{lo:.2f},{hi:.2f})", "n": len(b),
                          "human_execute_rate": round(sum(r["human_exec"] for r in b) / len(b), 2)})
    fails_false_pass = [r for r in rows if r["v1_pred"] == 0 and r["human_exec"] == 1]
    fails_over_pass = [r for r in rows if r["v1_pred"] == 1 and r["human_exec"] == 0]
    return {"n": len(rows), "structural_agreement": f"{struct_ok}/{len(all_rows)}",
            "trade": trade_tot, "pass": pass_tot,
            "confusion": {"TP": tp, "TN": tn, "FP": fp, "FN": fn},
            "raw_agreement": None if agree is None else round(agree, 3),
            "balanced_accuracy": None if bal_acc is None else round(bal_acc, 3),
            "pass_recall": None if pass_recall is None else round(pass_recall, 3),
            "trade_kept": None if trade_kept is None else round(trade_kept, 3),
            "false_pass": None if false_pass is None else round(false_pass, 3),
            "over_pass": None if over_pass is None else round(over_pass, 3),
            "criterion": CRIT, "PASSED": bool(passed), "calibration": calib,
            "false_pass_cases": [_case(r) for r in fails_false_pass],
            "over_pass_cases": [_case(r) for r in fails_over_pass]}


def _case(r) -> dict:
    return {"scene_id": r["scene_id"], "q": r["q"], "pd_location": r["pd_location"],
            "ce_distance": r["ce_distance"], "weakest": r["weakest"],
            "engine_direction": r["engine_direction"], "would_execute": r["would_execute"]}


def _md(s) -> str:
    L = [f"# Execution v1 — Batch-3 validation gate ({'PASS ✅' if s['PASSED'] else 'FAIL ❌'})", "",
         f"- structural agreement (direction vs engine): **{s['structural_agreement']}**",
         f"- execution label = would_execute; n = {s['n']}  "
         f"({s['trade']} human would-EXECUTE / {s['pass']} would-PASS)",
         f"- confusion (v1 vs would_execute): {s['confusion']}", "",
         "## Metrics vs pre-registered criterion", "",
         "| metric | value | threshold | ok |", "|---|---|---|---|",
         f"| balanced accuracy | {s['balanced_accuracy']} | ≥ {CRIT['balanced_accuracy']} | "
         f"{'✅' if s['balanced_accuracy'] and s['balanced_accuracy'] >= CRIT['balanced_accuracy'] else '❌'} |",
         f"| false-PASS (good trades blocked) | {s['false_pass']} | ≤ {CRIT['false_pass']} | "
         f"{'✅' if s['false_pass'] is not None and s['false_pass'] <= CRIT['false_pass'] else '❌'} |",
         f"| PASS recall | {s['pass_recall']} | ≥ {CRIT['pass_recall']} | "
         f"{'✅' if s['pass_recall'] and s['pass_recall'] >= CRIT['pass_recall'] else '❌'} |",
         f"| (raw agreement) | {s['raw_agreement']} | — | |",
         f"| (trade kept) | {s['trade_kept']} | — | |",
         f"| (over-PASS) | {s['over_pass']} | — | |", "",
         "## Calibration (v1 q-bin → observed human would-EXECUTE rate)", ""]
    for c in s["calibration"]:
        L.append(f"- {c['bin']}  n={c['n']}  human execute rate={c['human_execute_rate']}")
    L += ["", f"## False-PASS cases (v1 said PASS, human would EXECUTE) — {len(s['false_pass_cases'])}", ""]
    for c in s["false_pass_cases"]:
        L.append(f"- {c['scene_id']}  q={c['q']} pd={c['pd_location']} ce={c['ce_distance']} "
                 f"dir={c['engine_direction']} weakest={c['weakest']}")
    L += ["", f"## Over-PASS cases (v1 said TRADE, human would PASS) — {len(s['over_pass_cases'])}", ""]
    for c in s["over_pass_cases"]:
        L.append(f"- {c['scene_id']}  q={c['q']} pd={c['pd_location']} ce={c['ce_distance']} "
                 f"dir={c['engine_direction']} weakest={c['weakest']}")
    return "\n".join(L) + "\n"


def run() -> dict:
    rows = collect()
    s = score(rows)
    Path(RESULT_MD).write_text(_md(s))
    s["saved"] = RESULT_MD
    return s


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
