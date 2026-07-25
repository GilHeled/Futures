"""
Phase 1 — the pre-registered Target-A evaluation on MES DEV only.

Purged+embargoed expanding walk-forward (6 annual folds). For each outer fold:
  1. select the operative baseline on TRAIN QLIKE (fixed for the test fold);
  2. fit the single Ridge model with nested-CV α on TRAIN; predict the test fold.
Pooled out-of-sample forecasts then feed the frozen metrics and Go/No-Go (§7–§13),
plus the LMP diagnostic (§10). The locked hold-out is NEVER touched here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_state import baselines as B
from market_state import bootstrap as BS
from market_state import config as C
from market_state import metrics as M
from market_state import model as MODEL
from market_state.data import annotate_session, load_bars
from market_state.features import FEATURES, compute_features
from market_state.labels import build_label_frame


def _assemble(symbol: str = C.DEV_INSTRUMENT, variance: str = "squared_return"):
    bars = annotate_session(load_bars(symbol, split="dev"))
    frame = build_label_frame(bars, variance=variance)
    feats = compute_features(bars, variance=variance).reindex(frame.index)
    for c in FEATURES:
        frame[c] = feats[c].values
    feat_ok = np.all(np.isfinite(feats[list(FEATURES)].values), axis=1)
    frame["sample_ok"] = frame["sample"].values & feat_ok
    return frame


def run(symbol: str = C.DEV_INSTRUMENT, variance: str = "squared_return") -> dict:
    frame = _assemble(symbol, variance)
    Xall = frame[list(FEATURES)].values
    sample_pos = np.where(frame["sample_ok"].values)[0]          # positions in full frame
    entry = frame["pos"].values[sample_pos]
    exit_ = frame["exit_pos"].values[sample_pos]

    rec = {k: [] for k in ("date", "year", "rv", "y", "m_var", "m_log",
                           "b_var", "b_log", "t_var", "t_log", "lmp", "regime_var")}
    base_names, alphas, smears = [], [], []

    from market_state.purged_cv import purged_walk_forward_splits
    outer = list(purged_walk_forward_splits(entry, exit_, C.N_SPLITS, C.EMBARGO_BARS))

    et_dates = pd.Index(frame["et_date"].values)
    years = pd.Index([d.year for d in frame["et_date"].values])

    for tr_s, te_s in outer:
        full_train_mask = np.zeros(len(frame), dtype=bool)
        full_train_mask[sample_pos[tr_s]] = True

        sel = B.select_baseline(frame, full_train_mask)
        base = sel["forecasts"][sel["selected"]]
        tod = sel["forecasts"]["time_of_day"]

        te_full = sample_pos[te_s]
        log_pred, var_pred, best_alpha, s_f, _ = MODEL.fit_predict(
            Xall[sample_pos[tr_s]], frame["log_rv"].values[sample_pos[tr_s]],
            frame["rv"].values[sample_pos[tr_s]], entry[tr_s], exit_[tr_s],
            Xall[te_full],
        )
        base_names.append(sel["selected"])
        alphas.append(best_alpha)
        smears.append(s_f)

        rec["date"].extend(et_dates[te_full])
        rec["year"].extend(years[te_full])
        rec["rv"].extend(frame["rv"].values[te_full])
        rec["y"].extend(frame["log_rv"].values[te_full])
        rec["m_var"].extend(var_pred)
        rec["m_log"].extend(log_pred)
        rec["b_var"].extend(base["var"].values[te_full])
        rec["b_log"].extend(base["log"].values[te_full])
        rec["t_var"].extend(tod["var"].values[te_full])
        rec["t_log"].extend(tod["log"].values[te_full])
        rec["lmp"].extend(frame["lmp_event"].values[te_full])
        rec["regime_var"].extend(frame["rv_prev_session"].values[te_full])   # §12 regime stratifier

    d = {k: np.asarray(v, dtype=float) if k not in ("date",) else np.asarray(v)
         for k, v in rec.items()}
    res = _evaluate(d, base_names, alphas, smears)
    res["_raw"] = d
    return res


def _evaluate(d, base_names, alphas, smears) -> dict:
    rv, y = d["rv"], d["y"]
    m_var, m_log, b_var, b_log, t_var, t_log = (
        d["m_var"], d["m_log"], d["b_var"], d["b_log"], d["t_var"], d["t_log"])

    model_q = M.qlike(rv, m_var)
    base_q = M.qlike(rv, b_var)
    tod_q = M.qlike(rv, t_var)
    qred_base = M.qlike_reduction(base_q, model_q)
    qred_tod = M.qlike_reduction(tod_q, model_q)

    inc_r2 = M.incremental_r2(y, m_log, b_log)
    r2_mean = M.r2_vs_mean(y, m_log)
    mz = M.mincer_zarnowitz(y, m_log)
    mse = M.log_rv_mse(y, m_log)
    mae = M.log_rv_mae(y, m_log)

    # per-year QLIKE reduction vs the selected baseline
    per_year = {}
    for yr in np.unique(d["year"]):
        m = d["year"] == yr
        per_year[int(yr)] = M.qlike_reduction(M.qlike(rv[m], b_var[m]), M.qlike(rv[m], m_var[m]))
    yr_vals = np.array(list(per_year.values()))
    years_positive = int(np.sum(yr_vals > 0))
    drop_best_mean = float(np.mean(np.sort(yr_vals)[:-1])) if len(yr_vals) > 1 else float(yr_vals[0])

    # block bootstrap on daily paired QLIKE improvement (baseline - model)
    base_contrib = M.qlike_contributions(rv, b_var)
    model_contrib = M.qlike_contributions(rv, m_var)
    days, imp = BS.daily_paired_improvement(d["date"], base_contrib, model_contrib)
    boot = BS.block_bootstrap_mean(imp)

    # LMP diagnostic (§10, v2): the LMP score is the LOG point forecast μ̂
    # (pre-retransformation), NOT the fold-smeared variance forecast — because s_f
    # varies by fold, pooled ranks of the smeared forecast are not invariant.
    lmp = d["lmp"]
    lmp_auc_model = M.auc(m_log, lmp)
    lmp_auc_base = M.auc(b_log, lmp)
    lmp_rel = M.decile_reliability(m_log, lmp)

    gng = C.GO_NO_GO
    passes = {
        "qlike_reduction>=margin": qred_base >= gng.min_qlike_reduction,
        "bootstrap_p<=0.05": boot["p_mean_le_zero"] <= gng.max_prob_improvement_le_zero,
        "incremental_log_rv_r2>0": inc_r2 > 0,
        "mz_slope_in_band": gng.mz_slope_lo <= mz["slope"] <= gng.mz_slope_hi,
        "years_positive>=4": years_positive >= gng.min_years_positive,
        "drop_best_year>0": drop_best_mean > 0,
        "beats_time_of_day": qred_tod > 0,
    }
    go = all(passes.values())

    return {
        "n_obs": int(len(rv)), "n_days": boot["n_days"],
        "model_qlike": model_q, "base_qlike": base_q, "tod_qlike": tod_q,
        "qlike_reduction_vs_base": qred_base, "qlike_reduction_vs_tod": qred_tod,
        "incremental_log_rv_r2": inc_r2, "r2_vs_mean": r2_mean,
        "mz": mz, "log_rv_mse": mse, "log_rv_mae": mae,
        "per_year_reduction": per_year, "years_positive": years_positive,
        "drop_best_year_mean": drop_best_mean,
        "bootstrap": boot,
        "lmp_auc_model": lmp_auc_model, "lmp_auc_base": lmp_auc_base,
        "lmp_reliability": lmp_rel,
        "selected_baselines": base_names, "alphas": alphas,
        "smearing_factors": smears,
        "go_no_go": passes, "GO": go,
    }


def format_report(r: dict) -> str:
    L = []
    L.append("=" * 70)
    L.append("PHASE 1 v2 — Target A (Expected Realized Volatility), MES DEV")
    L.append("Duan smearing retransformation (SMEARING_SCOPE=all_qlike)")
    L.append("=" * 70)
    L.append(f"observations={r['n_obs']}  days={r['n_days']}  folds={C.N_SPLITS}")
    L.append(f"selected baselines per fold: {r['selected_baselines']}")
    L.append(f"chosen alphas per fold:      {r['alphas']}")
    L.append(f"model smearing factors s_f:  {[round(s,4) for s in r['smearing_factors']]}")
    L.append("")
    L.append("--- QLIKE (lower is better) ---")
    L.append(f"model={r['model_qlike']:.6f}  selected-baseline={r['base_qlike']:.6f}  "
             f"time-of-day={r['tod_qlike']:.6f}")
    L.append(f"QLIKE reduction vs baseline = {r['qlike_reduction_vs_base']*100:+.2f}%  "
             f"(margin {C.GO_NO_GO.min_qlike_reduction*100:.1f}%)")
    L.append(f"QLIKE reduction vs time-of-day = {r['qlike_reduction_vs_tod']*100:+.2f}%")
    L.append("")
    L.append("--- secondary ---")
    L.append(f"incremental log-RV R² vs baseline = {r['incremental_log_rv_r2']:+.4f}")
    L.append(f"R² of log-RV about mean           = {r['r2_vs_mean']:+.4f}")
    L.append(f"Mincer-Zarnowitz: slope={r['mz']['slope']:.3f} intercept={r['mz']['intercept']:.3f} "
             f"r2={r['mz']['r2']:.3f}   (band [{C.GO_NO_GO.mz_slope_lo},{C.GO_NO_GO.mz_slope_hi}])")
    L.append(f"log-RV MSE={r['log_rv_mse']:.4f}  MAE={r['log_rv_mae']:.4f}")
    L.append("")
    L.append("--- temporal stability (per-year QLIKE reduction vs baseline) ---")
    for yr, v in r["per_year_reduction"].items():
        L.append(f"  {yr}: {v*100:+.2f}%")
    L.append(f"years positive = {r['years_positive']} / {len(r['per_year_reduction'])}  "
             f"drop-best-year mean = {r['drop_best_year_mean']*100:+.2f}%")
    L.append("")
    L.append("--- significance (day-level block bootstrap on paired QLIKE improvement) ---")
    b = r["bootstrap"]
    L.append(f"mean daily improvement={b['mean']:.6e}  P(improvement<=0)={b['p_mean_le_zero']:.4f}  "
             f"95% CI=[{b['ci_lo']:.2e},{b['ci_hi']:.2e}]")
    L.append("")
    L.append("--- LMP diagnostic (report-only, not a gate; score = μ̂, pre-retransform) ---")
    L.append(f"AUC(model μ̂ -> LMP event)          = {r['lmp_auc_model']:.4f}")
    L.append(f"AUC(baseline μ̂ -> LMP event)       = {r['lmp_auc_base']:.4f}")
    L.append("")
    L.append("--- GO / NO-GO ---")
    for k, v in r["go_no_go"].items():
        L.append(f"  [{'PASS' if v else 'FAIL'}] {k}")
    L.append("")
    L.append(f"RESULT: {'GO' if r['GO'] else 'NO-GO'}")
    L.append("=" * 70)
    return "\n".join(L)


if __name__ == "__main__":
    import pathlib
    res = run()
    report = format_report(res)
    print(report)
    out = pathlib.Path("market_state/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "phase1_v2_mes_dev.txt").write_text(report + "\n")
