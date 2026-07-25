"""
THE SINGLE, PRE-REGISTERED HOLD-OUT CONFIRMATION for Target A v2 (§6/§13).

Runs exactly ONCE. Trains on the FULL development set using the identical frozen
procedures (baseline selection on dev-train QLIKE; Ridge with nested-CV α; Duan
smearing factor from leakage-safe OOF dev residuals) and evaluates on the LOCKED
hold-out (2025-01-01 → 2026-07-09). No implementation, hyperparameter, baseline,
calibration, threshold, or reporting change of any kind.

GO (§6/§13): QLIKE reduction vs the selected baseline is POSITIVE and
block-bootstrap-significant (P(improvement≤0) ≤ 0.05) on the hold-out.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from market_state import baselines as B
from market_state import config as C
from market_state import metrics as M
from market_state import model as MODEL
from market_state.data import annotate_session, load_bars
from market_state.features import FEATURES, compute_features
from market_state.labels import build_label_frame
from market_state.phase1 import _evaluate


def _assemble_all(symbol: str = C.DEV_INSTRUMENT):
    # split="all" requires the explicit hold-out opt-in — this is the ONE place it is used.
    bars = annotate_session(load_bars(symbol, split="all", allow_holdout=True))
    frame = build_label_frame(bars)
    feats = compute_features(bars).reindex(frame.index)
    for c in FEATURES:
        frame[c] = feats[c].values
    feat_ok = np.all(np.isfinite(feats[list(FEATURES)].values), axis=1)
    frame["sample_ok"] = frame["sample"].values & feat_ok
    return frame


def run_holdout(symbol: str = C.DEV_INSTRUMENT) -> dict:
    frame = _assemble_all(symbol)
    is_dev = frame.index <= C.DEV_END
    is_hold = frame.index >= C.HOLDOUT_START
    dev_mask = frame["sample_ok"].values & is_dev
    dev_pos = np.where(dev_mask)[0]
    hold_pos = np.where(frame["sample_ok"].values & is_hold)[0]

    Xall = frame[list(FEATURES)].values

    # --- baseline selection on DEV train QLIKE (fixed for the hold-out) ---
    sel = B.select_baseline(frame, dev_mask)
    base = sel["forecasts"][sel["selected"]]
    tod = sel["forecasts"]["time_of_day"]

    # --- model: nested-CV α + OOF smearing on DEV, refit on full DEV, predict hold-out ---
    mu, var, alpha, s_f, _ = MODEL.fit_predict(
        Xall[dev_pos], frame["log_rv"].values[dev_pos], frame["rv"].values[dev_pos],
        frame["pos"].values[dev_pos], frame["exit_pos"].values[dev_pos], Xall[hold_pos])

    et_dates = pd.Index(frame["et_date"].values)
    years = pd.Index([d.year for d in frame["et_date"].values])
    d = {
        "date": np.asarray(et_dates[hold_pos]),
        "year": years[hold_pos].to_numpy(dtype=float),
        "rv": frame["rv"].values[hold_pos],
        "y": frame["log_rv"].values[hold_pos],
        "m_var": var, "m_log": mu,
        "b_var": base["var"].values[hold_pos], "b_log": base["log"].values[hold_pos],
        "t_var": tod["var"].values[hold_pos], "t_log": tod["log"].values[hold_pos],
        "lmp": frame["lmp_event"].values[hold_pos],
        "regime_var": frame["rv_prev_session"].values[hold_pos],
    }
    r = _evaluate(d, [sel["selected"]], [alpha], [s_f])

    # --- hold-out GO rule (§6/§13): positive AND block-bootstrap-significant ---
    r["HOLDOUT_GO"] = bool(
        r["qlike_reduction_vs_base"] > 0
        and r["bootstrap"]["p_mean_le_zero"] <= C.GO_NO_GO.holdout_max_prob_improvement_le_zero
    )
    r["selected_baseline"] = sel["selected"]
    r["alpha"] = alpha
    r["s_f"] = s_f
    r["_raw"] = d

    # --- baseline-selection detail: every candidate's DEV-train QLIKE + smearing s ---
    r["baseline_train_qlike"] = sel["train_qlike"]
    r["baseline_smears"] = {n: float(sel["forecasts"][n]["s"]) for n in C.CANDIDATE_BASELINES}

    # --- structural dev-only calibration guarantee (asserted) ---
    dev_dates = frame.index[dev_pos]
    hold_dates = frame.index[hold_pos]
    assert bool((dev_dates <= C.DEV_END).all())          # every training row is in dev
    assert bool((hold_dates >= C.HOLDOUT_START).all())   # every scored row is in hold-out
    r["calibration_rows"] = int(len(dev_pos))
    r["calibration_max_date"] = str(dev_dates.max())
    r["holdout_min_date"] = str(hold_dates.min())
    r["holdout_max_date"] = str(hold_dates.max())
    return r


def _monthly_breakdown(d) -> list:
    rv, m_var, b_var = d["rv"], d["m_var"], d["b_var"]
    months = np.array([f"{x.year}-{x.month:02d}" for x in d["date"]])
    rows = []
    for mth in sorted(set(months)):
        m = (months == mth) & np.isfinite(rv) & np.isfinite(m_var) & np.isfinite(b_var)
        red = M.qlike_reduction(M.qlike(rv[m], b_var[m]), M.qlike(rv[m], m_var[m]))
        rows.append((mth, int(m.sum()), red))
    return rows


def format_report(r: dict) -> str:
    L = []
    L.append("=" * 70)
    L.append("HOLD-OUT CONFIRMATION (SINGLE, FINAL) — Target A v2, MES")
    L.append("Hold-out: 2025-01-01 → 2026-07-09  (trained on full dev)")
    L.append("=" * 70)
    L.append(f"observations={r['n_obs']}  days={r['n_days']}")
    L.append(f"selected baseline (on dev) = {r['selected_baseline']}  α={r['alpha']}  s_f={r['s_f']:.4f}")
    L.append("")
    L.append("--- QLIKE (lower is better) ---")
    L.append(f"model={r['model_qlike']:.6f}  selected-baseline={r['base_qlike']:.6f}  "
             f"time-of-day={r['tod_qlike']:.6f}")
    L.append(f"QLIKE reduction vs baseline     = {r['qlike_reduction_vs_base']*100:+.2f}%")
    L.append(f"QLIKE reduction vs time-of-day  = {r['qlike_reduction_vs_tod']*100:+.2f}%")
    L.append("")
    L.append("--- baseline selection (chosen on DEV train QLIKE; fixed for hold-out) ---")
    L.append("  candidate       dev-train QLIKE   smearing s   selected")
    for name in C.CANDIDATE_BASELINES:
        tq = r["baseline_train_qlike"][name]
        sb = r["baseline_smears"][name]
        mark = "  <-- selected" if name == r["selected_baseline"] else ""
        L.append(f"  {name:<14} {tq:>14.6f}   {sb:>9.4f}{mark}")
    L.append("")
    L.append("--- smearing factors (Duan retransformation h = s·exp(μ̂)) ---")
    L.append(f"  model s_f (OOF on dev)            = {r['s_f']:.4f}")
    for name in ("har", "time_of_day"):
        L.append(f"  baseline '{name}' s_b (OOF on dev) = {r['baseline_smears'][name]:.4f}")
    L.append("  persistence / ewma               = 1.0 (variance-space; no retransform)")
    L.append("")
    L.append("--- significance (day-level block bootstrap on paired QLIKE improvement) ---")
    b = r["bootstrap"]
    L.append(f"config: block_days={C.BOOTSTRAP_BLOCK_DAYS}  resamples={C.BOOTSTRAP_RESAMPLES}  "
             f"seed={C.BOOTSTRAP_SEED}  unit=per-day paired (baseline−model) QLIKE contribution")
    L.append(f"mean daily improvement={b['mean']:.6e}  P(improvement<=0)={b['p_mean_le_zero']:.4f}  "
             f"95% CI=[{b['ci_lo']:.2e},{b['ci_hi']:.2e}]  n_days={b['n_days']}")
    L.append("")
    L.append("--- secondary (reported) ---")
    L.append(f"incremental log-RV R² vs baseline = {r['incremental_log_rv_r2']:+.4f}")
    L.append(f"R² of log-RV about mean           = {r['r2_vs_mean']:+.4f}")
    L.append(f"log-RV MSE={r['log_rv_mse']:.4f}  MAE={r['log_rv_mae']:.4f}")
    L.append(f"Mincer-Zarnowitz: slope={r['mz']['slope']:.4f}  intercept={r['mz']['intercept']:.4f}  "
             f"r2={r['mz']['r2']:.4f}")
    L.append(f"LMP AUC (model μ̂) = {r['lmp_auc_model']:.4f}   baseline μ̂ = {r['lmp_auc_base']:.4f}  (report-only)")
    L.append("")
    L.append("--- annual QLIKE reduction vs baseline ---")
    for yr, v in r["per_year_reduction"].items():
        L.append(f"    {int(yr)}: {v*100:+.2f}%")
    L.append("")
    L.append("--- monthly QLIKE reduction vs baseline ---")
    for mth, n, red in _monthly_breakdown(r["_raw"]):
        L.append(f"    {mth} (n={n:5d}): {red*100:+.2f}%")
    L.append("")
    L.append("--- dev-only calibration guarantee (asserted in code) ---")
    L.append(f"  training/calibration rows        = {r['calibration_rows']} (all in dev)")
    L.append(f"  latest calibration timestamp     = {r['calibration_max_date']}  (<= dev end {C.DEV_END.date()})")
    L.append(f"  hold-out scored window           = {r['holdout_min_date']} .. {r['holdout_max_date']}")
    L.append(f"  model α, s_f and every baseline s_b estimated on dev rows ONLY;")
    L.append(f"  NO hold-out observation entered α-selection, smearing, or baseline fitting.")
    L.append("")
    L.append("--- HOLD-OUT DECISION (§6/§13: reduction > 0 AND bootstrap P ≤ 0.05) ---")
    L.append(f"  QLIKE reduction > 0        : {'PASS' if r['qlike_reduction_vs_base'] > 0 else 'FAIL'}")
    L.append(f"  bootstrap P ≤ 0.05         : "
             f"{'PASS' if b['p_mean_le_zero'] <= C.GO_NO_GO.holdout_max_prob_improvement_le_zero else 'FAIL'}")
    L.append("")
    L.append(f"RESULT: {'GO (hold-out confirms)' if r['HOLDOUT_GO'] else 'NO-GO (hold-out rejects)'}")
    L.append("=" * 70)
    return "\n".join(L)


if __name__ == "__main__":
    res = run_holdout()
    report = format_report(res)
    print(report)
    out = pathlib.Path("market_state/results")
    out.mkdir(parents=True, exist_ok=True)
    # preserve the original one-shot record; write the enriched (superset) report separately
    (out / "holdout_v2_mes_full.txt").write_text(report + "\n")
