"""First baseline ML — SHADOW MODE ONLY. Setup-quality / candidate-ranking, never raw BUY/SELL.

Task: given a candidate's CAUSAL ICT features (from the deterministic engine), predict the OUTCOME
label `target_before_stop` (did the setup reach its liquidity target before its stop?) on cleanly
resolved candidates. This is Model B (outcome) — trained on outcome labels, kept entirely separate
from features. It does NOT change any recommendation; it is reported alongside the deterministic
engine for comparison.

Discipline enforced here:
  * chronological TRAIN / VAL from the `split` field (train_end boundary; straddlers already purged);
  * the LOCKED OOS (>=2025-01-01) is never loaded;
  * ONLY scale-invariant causal features enter X (raw-price-scale fields dropped so MES/MNQ pool);
  * a hard assertion that no outcome/label key leaks into the feature matrix;
  * simple baselines first (LogisticRegression, RandomForest); report AUC + a full data audit.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

# scale-invariant, causal features only (raw-price fields intentionally excluded so instruments pool)
NUM = ["rr", "rank", "n_competing_setups", "dr_location_norm", "sweep_rank", "sweep_rejection",
       "mss_rank", "fvg_rank", "n_active_erl", "n_structural", "day_of_week"]
CAT = ["dr_zone", "session", "direction", "fvg_status", "mss_state", "sweep_direction"]
BOOL = ["actionable", "displacement_exhausted"]
BANNED = {"outcome", "labels", "course_execution", "fixed_r", "liquidity_target", "excursion",
          "session_end", "realized_R", "target_before_stop", "r2_before_stop", "course_result",
          "bars_until_roll"}


def load_clean(paths: list[str], label: str = "target_before_stop") -> list[dict]:
    rows = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            r = json.loads(line)
            if r.get("labels", {}).get(label) in (0, 1):
                rows.append(r)
    return rows


def _feat_row(r: dict) -> dict:
    f = r["features"]
    out = {}
    for k in NUM:
        out[k] = f.get(k)
    for k in CAT:
        out[k] = f.get(k) if f.get(k) is not None else "none"
    for k in BOOL:
        v = f.get(k, r.get(k))
        out[k] = 1 if v in (True, 1) else 0
    return out


def build_xy(rows: list[dict], label: str = "target_before_stop", drop: tuple = ()):
    import pandas as pd
    X = pd.DataFrame([_feat_row(r) for r in rows])
    assert BANNED.isdisjoint(X.columns), f"LEAKAGE: outcome key in features: {BANNED & set(X.columns)}"
    X = pd.get_dummies(X, columns=CAT, dummy_na=False)
    for c in NUM:
        X[c] = pd.to_numeric(X[c], errors="coerce")
        X[c] = X[c].fillna(X[c].median())
    if drop:
        X = X.drop(columns=[c for c in drop if c in X.columns])
    y = pd.Series([r["labels"][label] for r in rows], name="y")
    split = pd.Series([r["split"] for r in rows], name="split")
    meta = pd.DataFrame({"symbol": [r["symbol"] for r in rows],
                         "contract": [r["contract"] for r in rows],
                         "year": [int(r["decision_time"][:4]) for r in rows],
                         "actionable": [1 if r.get("actionable") else 0 for r in rows]})
    return X, y, split, meta


def audit(rows: list[dict], label: str = "target_before_stop") -> dict:
    yr = Counter(int(r["decision_time"][:4]) for r in rows)
    sp = Counter(r["split"] for r in rows)
    ct = Counter(r["contract"] for r in rows)
    tf = Counter(r["signal_tf"] for r in rows)
    cls = Counter(r["labels"][label] for r in rows)
    miss = Counter()
    for r in rows:
        for k in NUM + CAT:
            if r["features"].get(k) is None:
                miss[k] += 1
    return {"n": len(rows), "class_balance": dict(cls), "by_year": dict(sorted(yr.items())),
            "by_split": dict(sp), "by_contract": dict(ct), "by_tf": dict(tf),
            "feature_missingness": dict(miss)}


def run(paths: list[str], label: str = "target_before_stop", drop: tuple = ()) -> dict:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score, accuracy_score
    from sklearn.preprocessing import StandardScaler

    rows = load_clean(paths, label)
    rep = {"label": label, "dropped_features": list(drop), "audit": audit(rows, label)}
    X, y, split, meta = build_xy(rows, label, drop)
    tr, va = (split == "train").values, (split == "val").values
    Xtr, Xva, ytr, yva = X[tr], X[va], y[tr], y[va]
    rep["n_train"], rep["n_val"] = int(tr.sum()), int(va.sum())
    rep["val_positive_rate"] = round(float(yva.mean()), 4)

    # baselines
    maj = 1 if ytr.mean() >= 0.5 else 0
    rep["baseline_majority_acc"] = round(float((yva == maj).mean()), 4)
    # deterministic "actionable" gate as a classifier of success
    act = meta["actionable"].values[va]
    if act.sum() > 0 and act.sum() < len(act):
        rep["engine_actionable_auc"] = round(float(roc_auc_score(yva, act)), 4)

    # Logistic Regression (scaled)
    sc = StandardScaler().fit(Xtr)
    lr = LogisticRegression(max_iter=2000, class_weight="balanced").fit(sc.transform(Xtr), ytr)
    p_lr = lr.predict_proba(sc.transform(Xva))[:, 1]
    rep["logreg"] = {"val_auc": round(float(roc_auc_score(yva, p_lr)), 4),
                     "val_acc": round(float(accuracy_score(yva, (p_lr >= 0.5).astype(int))), 4)}
    coefs = sorted(zip(X.columns, lr.coef_[0]), key=lambda kv: -abs(kv[1]))[:12]
    rep["logreg"]["top_coefficients"] = [(c, round(float(w), 3)) for c, w in coefs]

    # Random Forest
    rf = RandomForestClassifier(n_estimators=300, min_samples_leaf=20, class_weight="balanced",
                                random_state=0, n_jobs=-1).fit(Xtr, ytr)
    p_rf = rf.predict_proba(Xva)[:, 1]
    rep["random_forest"] = {"val_auc": round(float(roc_auc_score(yva, p_rf)), 4),
                            "val_acc": round(float(accuracy_score(yva, (p_rf >= 0.5).astype(int))), 4)}
    imp = sorted(zip(X.columns, rf.feature_importances_), key=lambda kv: -kv[1])[:12]
    rep["random_forest"]["top_importances"] = [(c, round(float(w), 4)) for c, w in imp]
    rep["shadow_mode"] = "predictions are reported only; they do NOT change engine recommendations"
    return rep


if __name__ == "__main__":
    import sys
    print(json.dumps(run(sys.argv[1:]), indent=1, default=str))
