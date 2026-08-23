"""SUPERSEDED (2026-08-22): the engine no longer uses a fitted/logistic execution filter. Execution
v1 is a fixed, transparent weighted mean in engine/execution_quality.py (0.6·pd_location +
0.4·ce_distance) — no ML. This module is retained only as the historical v0 calibration record.

Calibrate the MVP execution-quality filter from human annotations (Batch-1 TRADE vs PASS).

TRADE label = the human took a direction (LONG/SHORT); PASS = NO_TRADE. We reproduce each labelled
scene through the (unchanged) engine, compute the five execution factors, report how each factor
separates TRADE from PASS, fit a transparent logistic (weights + threshold), and persist v0. The
engine is never modified; this only produces the execution recommendation layer.
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import execution_quality as EQ
from ict_live.research import annotation_app as APP

FILTER_PATH = "ict_live/research/datasets/execution_filter_v0.json"


def _label(a) -> int:
    return 0 if any(d == "NO_TRADE" for d in a["decisions"]) else 1     # PASS=0, TRADE=1


def build_calibration(fidelity_path="ict_live/research/datasets/human_fidelity.jsonl") -> list[dict]:
    ann = {}
    for line in Path(fidelity_path).read_text().splitlines():
        a = json.loads(line)
        ann[a["scene_id"]] = a                        # latest per scene
    rows = []
    for sid, a in ann.items():
        try:
            ms, bar = APP.build_state({"symbol": a["symbol"], "contract": a["contract"],
                                       "signal_tf": a["signal_tf"], "time": a["candidate_time"]})
        except SystemExit:
            continue
        if ms is None:
            continue
        f = EQ.factors(ms)
        if f is None:                                 # engine has no live setup here
            continue
        rows.append({"scene_id": sid, "label": _label(a), **f})
    return rows


def calibrate(rows: list[dict]) -> dict:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    X = np.array([[r[k] for k in EQ.FACTOR_NAMES] for r in rows])
    y = np.array([r["label"] for r in rows])
    rep = {"n": len(y), "trade": int(y.sum()), "pass": int(len(y) - y.sum())}
    # per-factor separation (mean on TRADE vs PASS)
    rep["separation"] = {k: {"trade_mean": round(float(X[y == 1, i].mean()), 3) if (y == 1).any() else None,
                             "pass_mean": round(float(X[y == 0, i].mean()), 3) if (y == 0).any() else None}
                         for i, k in enumerate(EQ.FACTOR_NAMES)}
    if rep["pass"] < 2 or rep["trade"] < 2:
        rep["status"] = "insufficient_minority — equal weights / default threshold"
        rep["model"] = None
        return rep
    m = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X, y)
    coef = {k: round(float(c), 3) for k, c in zip(EQ.FACTOR_NAMES, m.coef_[0])}
    rep["logistic_coef"] = coef
    rep["model"] = {"coef": coef, "intercept": round(float(m.intercept_[0]), 3), "threshold": 0.5}
    # honest leave-one-out CV confusion (train fit overfits with only ~5 PASS)
    from sklearn.model_selection import LeaveOneOut
    loo = LeaveOneOut(); pred = np.zeros(len(y))
    for tr, te in loo.split(X):
        if len(set(y[tr])) < 2:
            pred[te] = y[tr].mean(); continue
        mm = LogisticRegression(max_iter=2000, class_weight="balanced").fit(X[tr], y[tr])
        pred[te] = mm.predict_proba(X[te])[0, 1]
    p = (pred >= 0.5).astype(int)
    rep["status"] = "calibrated (logistic; v0 MVP — provisional, refine with Batch-2)"
    rep["loo_cv_confusion"] = {"trade_pred_trade": int(((p == 1) & (y == 1)).sum()),
                               "pass_pred_pass": int(((p == 0) & (y == 0)).sum()),
                               "pass_pred_trade": int(((p == 1) & (y == 0)).sum()),
                               "trade_pred_pass": int(((p == 0) & (y == 1)).sum())}
    rep["loo_pass_recall"] = (rep["loo_cv_confusion"]["pass_pred_pass"] / rep["pass"]) if rep["pass"] else None
    return rep


def run() -> dict:
    rows = build_calibration()
    rep = calibrate(rows)
    Path(FILTER_PATH).write_text(json.dumps(
        {"version": "v0", "model": rep.get("model"),
         "calibration": {k: rep[k] for k in rep if k != "model"}}, indent=1))
    rep["saved"] = FILTER_PATH
    return rep


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
