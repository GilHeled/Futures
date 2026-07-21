"""
Phase-1a POST-MORTEM (diagnostic only — NOT a new experiment; changes no
model/feature/parameter/threshold, never touches the hold-out or the other
instruments). Explains WHY the frozen MES-dev experiment failed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, log_loss
from sklearn.preprocessing import StandardScaler

from intraday_alerts import config as C
from intraday_alerts.data import annotate_session, load_bars
from intraday_alerts.ev import expected_value, round_trip_cost
from intraday_alerts.features import compute_features
from intraday_alerts.labeling import AMBIGUOUS, DOWN, TIMEOUT, UP, label_triple_barrier, session_exit_positions
from intraday_alerts.phase1a import N_FOLDS, _fit_predict, _proba_col
from intraday_alerts.purged_cv import purged_walk_forward_splits
from mnq_system.cli import _resolve_contract_spec
from mnq_system.indicators import atr as atr_fn

pd.set_option("display.width", 160)
np.set_printoptions(suppress=True, precision=4)


def hist(x, lo, hi, bins=10, label=""):
    x = np.asarray(x); x = x[np.isfinite(x)]
    edges = np.linspace(lo, hi, bins + 1)
    counts, _ = np.histogram(x, bins=edges)
    print(f"  {label} (n={len(x)}):")
    for i in range(bins):
        bar = "#" * int(60 * counts[i] / max(counts.max(), 1))
        print(f"    [{edges[i]:+.2f},{edges[i+1]:+.2f}) {counts[i]:7d} {bar}")


def main():
    spec = _resolve_contract_spec("MES"); tick, pv = spec.tick_size, spec.point_value
    rt = round_trip_cost(C.COMMISSION_PER_RT, C.SPREAD_TICKS, C.SLIPPAGE_TICKS, tick, pv)
    k, hold = C.PRIMARY_BARRIER["k"], C.PRIMARY_BARRIER["hold_bars"]
    bars = load_bars("MES", "dev"); ann = annotate_session(bars); atr = atr_fn(bars, period=C.ATR_PERIOD)
    close, high, low = bars["close"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy()
    ee = ann["entry_eligible"].to_numpy(); etd = np.array(list(ann["et_date"].to_numpy()))
    sxp = session_exit_positions(ann["force_flat"].to_numpy(), etd)
    feats = compute_features(bars, atr)

    lab = label_triple_barrier(high, low, close, atr.to_numpy(), ee, sxp, k=k, hold_bars=hold)
    fa = feats.to_numpy(); ep = lab["entry_pos"].to_numpy()
    fin = np.isfinite(fa[ep]).all(axis=1)
    lab = lab[fin].reset_index(drop=True); ep = lab["entry_pos"].to_numpy()
    X = fa[ep]; y = lab["label"].to_numpy(); xp = lab["exit_pos"].to_numpy(); tret = lab["tret"].to_numpy()
    atr_e = atr.to_numpy()[ep]
    print(f"=== A. SETUP === MES dev, entries(finite)={len(lab)}, barrier k={k}·ATR, hold={hold} bars, rt_cost=${rt:.2f}")
    print(f"  median ATR={np.nanmedian(atr_e):.3f}pts (${np.nanmedian(atr_e)*pv:.2f})  label mix: "
          f"UP={100*(y==UP).mean():.1f}% DOWN={100*(y==DOWN).mean():.1f}% TIMEOUT={100*(y==TIMEOUT).mean():.1f}% AMB={100*(y==AMBIGUOUS).mean():.1f}%")

    # ---- A. feature summary ----
    print("\n=== A. FEATURE SUMMARY (finite entry rows) ===")
    fdf = pd.DataFrame(X, columns=list(C.FEATURES))
    print(fdf.describe(percentiles=[.01, .25, .5, .75, .99]).T.to_string(float_format=lambda v: f"{v:.3f}"))

    # ---- B. coefficients (diagnostic full-dev multinomial fit, standardized) ----
    tr_all = y != AMBIGUOUS
    sc = StandardScaler().fit(X[tr_all])
    clf = LogisticRegression(C=1.0, class_weight=C.CLASS_WEIGHT, max_iter=2000).fit(sc.transform(X[tr_all]), y[tr_all])
    coef = pd.DataFrame(clf.coef_.T, index=list(C.FEATURES),
                        columns=[{0: "UP", 1: "DOWN", 2: "TIMEOUT"}[c] for c in clf.classes_])
    print("\n=== B. MULTINOMIAL COEFFICIENTS (standardized; log-odds per +1 SD) ===")
    print(coef.to_string(float_format=lambda v: f"{v:+.4f}"))
    print(f"  max |coef| across all = {np.abs(clf.coef_).max():.4f}  (tiny ⇒ features barely move class log-odds)")

    # ---- C. OOS predictions over purged WF folds ----
    splits = list(purged_walk_forward_splits(ep, xp, N_FOLDS, embargo_bars=hold))
    P = np.full((len(lab), 3), np.nan); fold_of = np.full(len(lab), -1)
    per_fold = []
    et = bars.index.tz_convert(C.TIMEZONE)
    for fi, (tri, tei) in enumerate(splits):
        tr = tri[tr_all[tri]]
        if len(np.unique(y[tr])) < 2:
            continue
        proba, classes = _fit_predict(X[tr], y[tr], X[tei])
        for cls, colname in [(UP, 0), (DOWN, 1), (TIMEOUT, 2)]:
            P[tei, colname] = _proba_col(proba, classes, cls)
        fold_of[tei] = fi
        # train vs test log-loss (learning curve / under-vs-overfit)
        pr_tr, _ = _fit_predict(X[tr], y[tr], X[tr])
        ll_tr = log_loss(y[tr], pr_tr, labels=classes)
        te_lbl = y[tei][y[tei] != AMBIGUOUS]
        te_prb = proba[y[tei] != AMBIGUOUS]
        ll_te = log_loss(te_lbl, te_prb, labels=classes) if len(np.unique(te_lbl)) > 1 else np.nan
        acc_te = (proba.argmax(1) == np.searchsorted(classes, y[tei])).mean()
        per_fold.append((fi, len(tr), len(tei), ll_tr, ll_te,
                         float(np.nanmean(P[tei, 0] - P[tei, 1]))))
    uniform_ll = -np.log(1/3)

    print("\n=== C. PROBABILITY HISTOGRAMS (OOS) ===")
    scored = np.isfinite(P[:, 0])
    hist(P[scored, 0], 0, 1, label="P(UP)")
    hist(P[scored, 1], 0, 1, label="P(DOWN)")
    hist(P[scored, 2], 0, 1, label="P(TIMEOUT)")
    print(f"  mean probs: UP={P[scored,0].mean():.3f} DOWN={P[scored,1].mean():.3f} TIMEOUT={P[scored,2].mean():.3f}  (uniform=0.333)")

    # ---- calibration for P(UP) ----
    print("\n=== C. CALIBRATION — P(UP) deciles vs empirical UP rate ===")
    pu = P[scored, 0]; yu = (y[scored] == UP).astype(float)
    q = pd.qcut(pu, 10, duplicates="drop")
    cal = pd.DataFrame({"pred": pu, "emp": yu}).groupby(q, observed=True).agg(["mean", "count"])
    print(cal.to_string(float_format=lambda v: f"{v:.3f}"))

    # ---- confusion matrix ----
    print("\n=== C. CONFUSION MATRIX (argmax vs true; non-ambiguous OOS) ===")
    mask = scored & (y != AMBIGUOUS)
    pred = P[mask].argmax(1); true = y[mask]
    cm = confusion_matrix(true, pred, labels=[UP, DOWN, TIMEOUT])
    print(pd.DataFrame(cm, index=["true UP", "true DOWN", "true TO"], columns=["pred UP", "pred DOWN", "pred TO"]).to_string())
    acc = (pred == true).mean(); base = max((true == UP).mean(), (true == DOWN).mean(), (true == TIMEOUT).mean())
    print(f"  accuracy={acc:.3f}  majority-base-rate={base:.3f}  (≈equal ⇒ no classification skill)")

    # ---- directional skill: does P(UP)>P(DOWN) predict realized UP? ----
    diff = P[mask, 0] - P[mask, 1]
    resolved = true != TIMEOUT
    lean_long = diff > 0
    up_rate_when_lean_long = (true[lean_long & resolved] == UP).mean()
    up_rate_when_lean_short = (true[(~lean_long) & resolved] == UP).mean()
    print(f"  KEY SKILL TEST: when model leans LONG (P_up>P_down), realized UP-rate among resolved = {up_rate_when_lean_long:.3f}")
    print(f"                  when model leans SHORT, realized UP-rate = {up_rate_when_lean_short:.3f}  (both ≈0.50 ⇒ no directional skill)")

    # ---- D. EV distribution before/after cost ----
    print("\n=== D. EV DISTRIBUTION (best side; OOS scored rows) ===")
    tret_hat = tret[tr_all & (y == TIMEOUT)].mean()
    ev_after, ev_before = [], []
    for j in np.where(scored)[0]:
        a = atr_e[j]
        if not np.isfinite(a) or a <= 0:
            continue
        ev_after.append(expected_value(P[j, 0], P[j, 1], P[j, 2], k, a, tret_hat, pv, rt).best_ev)
        ev_before.append(expected_value(P[j, 0], P[j, 1], P[j, 2], k, a, tret_hat, pv, 0.0).best_ev)
    ev_after, ev_before = np.array(ev_after), np.array(ev_before)
    for nm, arr, cost in [("AFTER cost", ev_after, rt), ("BEFORE cost (zero)", ev_before, 0.0)]:
        pos = (arr > 0).mean()
        print(f"  {nm}: mean=${arr.mean():+.2f} median=${np.median(arr):+.2f} max=${arr.max():+.2f} p99=${np.percentile(arr,99):+.2f}  frac>0={pos:.4%}")

    # ---- E. P(UP)-P(DOWN) vs required threshold ----
    print("\n=== E. P(UP)-P(DOWN) vs REQUIRED threshold ===")
    d_all = P[scored, 0] - P[scored, 1]
    hist(d_all, -0.5, 0.5, bins=10, label="P(UP)-P(DOWN)")
    req_no_to = rt / (pv * k * atr_e[scored])                       # ignoring timeout term
    req_with_to = (rt - pv * P[scored, 2] * tret_hat) / (pv * k * atr_e[scored])
    print(f"  required |P_up-P_down| (median, no-timeout term)   = {np.nanmedian(req_no_to):.3f}")
    print(f"  required |P_up-P_down| (median, with-timeout term)  = {np.nanmedian(req_with_to):.3f}")
    print(f"  observed |P_up-P_down|: p99={np.nanpercentile(np.abs(d_all),99):.3f} max={np.nanmax(np.abs(d_all)):.3f}")
    print(f"  rows where |diff| exceeds its required threshold: {np.mean(np.abs(d_all) > req_with_to):.4%}")

    # ---- F. per-fold ----
    print("\n=== F. PER-FOLD DIAGNOSTICS (learning curve; uniform log-loss=%.4f) ===" % uniform_ll)
    print("  fold  n_train  n_test  train_LL  test_LL   mean(Pup-Pdn)")
    for fi, ntr, nte, lltr, llte, md in per_fold:
        print(f"   {fi:>3d}  {ntr:7d}  {nte:6d}   {lltr:.4f}   {llte if np.isfinite(llte) else float('nan'):.4f}    {md:+.4f}")
    print("  interpretation: train_LL ≈ test_LL ≈ uniform ⇒ UNDERFIT/no-signal (nothing to overfit).")

    # ---- H. zero-cost realized edge (diagnostic-only counterfactual) ----
    print("\n=== H. ZERO-COST COUNTERFACTUAL (per-signal realized R, no policy) ===")
    r_fric = []
    for j in np.where(scored)[0]:
        a = atr_e[j]
        if not np.isfinite(a) or a <= 0:
            continue
        ev = expected_value(P[j, 0], P[j, 1], P[j, 2], k, a, tret_hat, pv, 0.0)
        side = ev.best_side
        if side is None:
            continue
        lb = y[j]
        if side == "long":
            r = {UP: +1.0, DOWN: -1.0, TIMEOUT: tret[j] / (k * a), AMBIGUOUS: -1.0}[lb]
        else:
            r = {DOWN: +1.0, UP: -1.0, TIMEOUT: -tret[j] / (k * a), AMBIGUOUS: -1.0}[lb]
        r_fric.append(r)
    r_fric = np.array(r_fric)
    print(f"  frictionless-selected signals: n={len(r_fric)}  mean R={r_fric.mean():+.4f}  median={np.median(r_fric):+.4f}")
    print(f"  win rate (R>0) = {(r_fric>0).mean():.3f}")
    print("  => mean R ≈ 0 ⇒ CONCLUSION 1 (no predictive signal even frictionless).")
    print("     mean R meaningfully > 0 ⇒ CONCLUSION 2 (signal exists but too small for costs).")


if __name__ == "__main__":
    main()
