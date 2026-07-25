"""
Pre-registered §12 DEV-only robustness for Target A v2 (confirmation only; no
methodological changes). Two analyses:
  1. Alternative RV proxy — repeat the frozen pipeline with a Garman–Klass range
     estimator as the RV measure; the QLIKE improvement should persist in sign.
  2. Regime stability — stratify the main (squared-return) run's out-of-sample
     forecasts by terciles of trailing-session realized volatility; the QLIKE
     improvement vs the selected baseline should hold in sign across regimes.
The locked hold-out is NOT touched.
"""
from __future__ import annotations

import pathlib

import numpy as np

from market_state import config as C
from market_state import metrics as M
from market_state import phase1


def _qred(rv, m_var, b_var) -> float:
    return M.qlike_reduction(M.qlike(rv, b_var), M.qlike(rv, m_var))


def regime_terciles(d) -> list:
    rv, m_var, b_var = d["rv"], d["m_var"], d["b_var"]
    reg = d["regime_var"]
    ok = np.isfinite(reg) & np.isfinite(rv) & np.isfinite(m_var) & np.isfinite(b_var)
    q1, q2 = np.quantile(reg[ok], [1 / 3, 2 / 3])
    rows = []
    for label, lo, hi in [("low", -np.inf, q1), ("mid", q1, q2), ("high", q2, np.inf)]:
        m = ok & (reg > lo) & (reg <= hi)
        rows.append((label, int(m.sum()), _qred(rv[m], m_var[m], b_var[m])))
    return rows


def main():
    sq = phase1.run(variance="squared_return")            # main, for regime stratification
    gk = phase1.run(variance="garman_klass")              # alternative RV proxy

    L = []
    L.append("=" * 70)
    L.append("PHASE 1 v2 — §12 DEV ROBUSTNESS (confirmation only), MES DEV")
    L.append("=" * 70)

    L.append("\n--- 1. Alternative RV proxy (Garman–Klass) ---")
    L.append(f"selected baselines per fold: {gk['selected_baselines']}")
    L.append(f"QLIKE reduction vs baseline = {gk['qlike_reduction_vs_base']*100:+.2f}% "
             f"(main squared-return run: {sq['qlike_reduction_vs_base']*100:+.2f}%)")
    L.append(f"bootstrap P(improvement<=0) = {gk['bootstrap']['p_mean_le_zero']:.4f}")
    L.append(f"years positive = {gk['years_positive']}/{len(gk['per_year_reduction'])}  "
             f"drop-best-year = {gk['drop_best_year_mean']*100:+.2f}%")
    L.append("  per-year QLIKE reduction:")
    for yr, v in gk["per_year_reduction"].items():
        L.append(f"    {yr}: {v*100:+.2f}%")
    L.append(f"  sign consistent with main result: "
             f"{np.sign(gk['qlike_reduction_vs_base']) == np.sign(sq['qlike_reduction_vs_base'])}")

    L.append("\n--- 2. Regime stability (terciles of trailing-session RV; main run) ---")
    rows = regime_terciles(sq["_raw"])
    for label, n, q in rows:
        L.append(f"  {label:>4} vol regime (n={n:6d}): QLIKE reduction vs baseline = {q*100:+.2f}%")
    all_pos = all(q > 0 for _, _, q in rows)
    L.append(f"  improvement positive in ALL regimes: {all_pos}")

    L.append("\n--- consistency verdict ---")
    consistent = (
        np.sign(gk["qlike_reduction_vs_base"]) == np.sign(sq["qlike_reduction_vs_base"])
        and gk["qlike_reduction_vs_base"] > 0
        and all_pos
    )
    L.append(f"All §12 dev robustness checks consistent with the main v2 result: {consistent}")
    L.append("=" * 70)

    report = "\n".join(L)
    print(report)
    out = pathlib.Path("market_state/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "phase1_v2_robustness_mes_dev.txt").write_text(report + "\n")


if __name__ == "__main__":
    main()
