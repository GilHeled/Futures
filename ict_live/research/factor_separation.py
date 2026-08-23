"""Factor-separation analysis — signal quality of the 5 execution factors, BEFORE fitting v1.

Reproduces the execution factors for every labelled scene (Batch-1 + Batch-2), then reports, per
factor: mean on TRADE vs PASS, separation strength (Cohen's d + single-factor AUC), correlation with
the take decision and with execute_live, and independence (factor-factor correlation + partial
correlation with the label controlling for pd_location + standardized multivariate logistic
coefficients). Purely descriptive — it does NOT fit or persist a v1 model.

Label convention (same as execution_filter): TRADE=1 if the human took a direction (LONG/SHORT),
PASS=0 if NO_TRADE. `execute_live` = would_execute (collected mainly in Batch-2).

Run under the venv: .venv/bin/python -m ict_live.research.factor_separation
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import annotations as anno
from ict_live.engine import execution_quality as EQ
from ict_live.research import annotation_app as APP

FIDELITY_PATH = "ict_live/research/datasets/human_fidelity.jsonl"
ROWS_CACHE = "ict_live/research/datasets/factor_rows.json"
RESULT_MD = "ict_live/research/RESULT_factor_separation.md"
FN = EQ.FACTOR_NAMES


def _take(rec) -> int:
    return 0 if "NO_TRADE" in rec["decisions"] else 1


def collect_rows(fidelity_path=FIDELITY_PATH, *, use_cache=True) -> list[dict]:
    """One row per labelled scene: the 5 factors + take/execute labels + context. Cached to disk."""
    if use_cache and Path(ROWS_CACHE).exists():
        return json.loads(Path(ROWS_CACHE).read_text())
    latest = {}
    for a in anno.load_annotations(fidelity_path):
        latest[a["scene_id"]] = a                    # append-only → last wins per scene
    rows, skipped = [], 0
    for i, (sid, a) in enumerate(latest.items(), 1):
        try:
            ms, _ = APP.build_state({"symbol": a["symbol"], "contract": a["contract"],
                                     "signal_tf": a["signal_tf"], "time": a["candidate_time"]})
        except SystemExit:
            ms = None
        f = EQ.factors(ms) if ms is not None else None
        if f is None:
            skipped += 1
            continue
        rows.append({"scene_id": sid, "round": a.get("provenance", {}).get("round"),
                     "take": _take(a), "would_execute": a.get("would_execute"),
                     "reason_for_pass": a.get("reason_for_pass", []),
                     "engine_direction": ms.recommendation.decision, **f})
        if i % 20 == 0:
            print(f"  reproduced {i}/{len(latest)} ({skipped} skipped no-setup)")
    Path(ROWS_CACHE).write_text(json.dumps(rows, indent=1))
    print(f"collected {len(rows)} rows ({skipped} skipped: no live setup)")
    return rows


def analyze(rows: list[dict]) -> dict:
    import numpy as np
    X = np.array([[r[k] for k in FN] for r in rows], float)
    take = np.array([r["take"] for r in rows], int)
    rep = {"n": len(rows), "trade": int(take.sum()), "pass": int((take == 0).sum())}
    rep["by_round"] = {}
    for rd in sorted({r["round"] for r in rows}, key=str):
        m = np.array([r["round"] == rd for r in rows])
        rep["by_round"][str(rd)] = {"n": int(m.sum()), "trade": int(take[m].sum()),
                                    "pass": int((take[m] == 0).sum())}

    def auc(x, y):                                   # P(x_pos > x_neg) via rank (Mann-Whitney)
        pos, neg = x[y == 1], x[y == 0]
        if len(pos) == 0 or len(neg) == 0:
            return None
        r = np.argsort(np.argsort(np.concatenate([pos, neg])))
        return float((r[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2) / (len(pos) * len(neg)))

    def pbr(x, y):                                   # point-biserial = Pearson(x, binary y)
        y = y.astype(float)
        if x.std() == 0 or y.std() == 0:
            return None
        return float(np.corrcoef(x, y)[0, 1])

    def cohen_d(x, y):
        a, b = x[y == 1], x[y == 0]
        s = np.sqrt(((len(a) - 1) * a.var(ddof=1) + (len(b) - 1) * b.var(ddof=1)) / (len(a) + len(b) - 2))
        return float((a.mean() - b.mean()) / s) if s > 0 else None

    we_mask = np.array([r["would_execute"] is not None for r in rows])
    we = np.array([1 if r["would_execute"] else 0 for r in rows])
    rep["execute_live_n"] = int(we_mask.sum())
    rep["factors"] = {}
    for i, k in enumerate(FN):
        x = X[:, i]
        rep["factors"][k] = {
            "mean_trade": round(float(x[take == 1].mean()), 4),
            "mean_pass": round(float(x[take == 0].mean()), 4),
            "cohen_d": None if cohen_d(x, take) is None else round(cohen_d(x, take), 3),
            "auc_take": None if auc(x, take) is None else round(auc(x, take), 3),
            "pbr_take": None if pbr(x, take) is None else round(pbr(x, take), 3),
            "pbr_execute": (round(pbr(x[we_mask], we[we_mask]), 3)
                            if we_mask.sum() > 2 and pbr(x[we_mask], we[we_mask]) is not None else None),
        }

    # factor-factor correlation matrix
    C = np.corrcoef(X.T)
    rep["corr_matrix"] = {FN[i]: {FN[j]: round(float(C[i, j]), 2) for j in range(len(FN))}
                          for i in range(len(FN))}

    # partial correlation of each factor with the take-label, controlling for pd_location
    pdl = X[:, FN.index("pd_location")]

    def resid(v, ctrl):
        A = np.vstack([np.ones_like(ctrl), ctrl]).T
        beta, *_ = np.linalg.lstsq(A, v, rcond=None)
        return v - A @ beta
    ry = resid(take.astype(float), pdl)
    rep["partial_corr_take_given_pd"] = {}
    for i, k in enumerate(FN):
        if k == "pd_location":
            continue
        rx = resid(X[:, i], pdl)
        pc = pbr(rx, ry) if rx.std() > 0 else None
        rep["partial_corr_take_given_pd"][k] = None if pc is None else round(pc, 3)

    # standardized multivariate logistic coefficients (unique contribution; descriptive only)
    from sklearn.linear_model import LogisticRegression
    Xs = (X - X.mean(0)) / (X.std(0) + 1e-9)
    m = LogisticRegression(max_iter=5000, class_weight="balanced").fit(Xs, take)
    rep["std_logit_coef"] = {k: round(float(c), 3) for k, c in zip(FN, m.coef_[0])}

    # candidate hard PASS rule: pd_location == 0  -> would it wrongly block TRADEs?
    pd0 = pdl <= 1e-9
    rep["pd_zero_rule"] = {
        "pd0_and_pass": int(((pd0) & (take == 0)).sum()),
        "pd0_and_trade": int(((pd0) & (take == 1)).sum()),    # these would be FALSE-blocked
        "pass_total": int((take == 0).sum()),
        "trade_total": int((take == 1).sum()),
        "pd0_by_round": {str(rd): int(((pd0) & np.array([r["round"] == rd for r in rows])).sum())
                         for rd in sorted({r["round"] for r in rows}, key=str)},
    }
    # reason_for_pass frequency among PASS rows
    import collections
    rc = collections.Counter()
    for r in rows:
        if r["take"] == 0:
            rc.update(r["reason_for_pass"])
    rep["reason_for_pass"] = dict(rc.most_common())
    return rep


def run() -> dict:
    rows = collect_rows()
    rep = analyze(rows)
    Path(RESULT_MD).write_text(_render_md(rep))
    rep["saved"] = RESULT_MD
    return rep


def _render_md(r: dict) -> str:
    L = ["# Execution-factor separation — Batch-1 + Batch-2 (descriptive; no v1 fit)", ""]
    L.append(f"- **n = {r['n']}**  ({r['trade']} TRADE / {r['pass']} PASS); "
             f"execute_live labelled on {r['execute_live_n']}")
    for rd, d in r["by_round"].items():
        L.append(f"  - {rd}: {d['n']} ({d['trade']} TRADE / {d['pass']} PASS)")
    L += ["", "## Per-factor separation", "",
          "| factor | mean TRADE | mean PASS | Cohen d | AUC(take) | r(take) | r(execute) |",
          "|---|---|---|---|---|---|---|"]
    for k, f in r["factors"].items():
        L.append(f"| {k} | {f['mean_trade']} | {f['mean_pass']} | {f['cohen_d']} | "
                 f"{f['auc_take']} | {f['pbr_take']} | {f['pbr_execute']} |")
    L += ["", "## Factor-factor correlation", "",
          "| | " + " | ".join(FN) + " |", "|" + "---|" * (len(FN) + 1)]
    for k in FN:
        L.append(f"| {k} | " + " | ".join(str(r["corr_matrix"][k][j]) for j in FN) + " |")
    L += ["", "## Partial correlation with take, controlling for pd_location", ""]
    for k, v in r["partial_corr_take_given_pd"].items():
        L.append(f"- {k}: {v}")
    L += ["", "## Standardized multivariate logistic coefficients (unique contribution)", ""]
    for k, v in r["std_logit_coef"].items():
        L.append(f"- {k}: {v}")
    pz = r["pd_zero_rule"]
    L += ["", "## Candidate hard rule: pd_location == 0 → PASS", "",
          f"- PASS with pd0: {pz['pd0_and_pass']}/{pz['pass_total']}",
          f"- TRADE with pd0 (would be FALSE-blocked): {pz['pd0_and_trade']}/{pz['trade_total']}",
          f"- pd0 by round: {pz['pd0_by_round']}"]
    L += ["", "## reason_for_pass frequency (PASS rows)", ""]
    for k, v in r["reason_for_pass"].items():
        L.append(f"- {k}: {v}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
