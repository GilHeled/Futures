# IMPLEMENTATION SPECIFICATION v2 — Target A (MES)

> **Status: FROZEN — 2026-07-24.** Defines exactly how the single retransformation
> is computed. All else is as in the approved `IMPLEMENTATION.md` (model, features,
> α-grid, mapping targets). Deterministic; leakage-safe; no researcher choice after
> seeing results.

## 7. Smearing retransformation — exact algorithm (per outer fold `f`)
Let `T_f` = outer-training rows (purged/embargoed), `E_f` = outer-test rows. `y = log(RV)`, `rv = RV`. `INNER_K = 5`, inner splits via `purged_walk_forward_splits(entry[T_f], exit[T_f], n_splits=INNER_K+1, embargo=EMBARGO_BARS)`.

### 7.1 Model (Ridge) — selection and calibration kept separate
1. **α-selection (nested OOF-QLIKE).** For each `α` in the grid: run the inner purged walk-forward over `T_f`; for each inner `(tr, val)` fit `StandardScaler`+`Ridge(α)` on `tr` and predict `μ̂_val`; pool the OOF `(μ̂_val, y_val, rv_val)` and residuals `ε = y_val − μ̂_val`; form the per-α OOF smearing factor `s_α = mean(exp(ε_pooled))`; score `inner_QLIKE(α) = QLIKE(rv_pooled, s_α · exp(μ̂_pooled))`. Choose `α* = argmin_α inner_QLIKE(α)`. *(The `s_α` here score α candidates on the retransformed objective; none is carried forward as the calibration factor.)*
2. **Calibration (dedicated OOF pass at α*).** Using `α*`, run one complete leakage-safe OOF pass over `T_f` (the inner purged walk-forward at `α*`), collecting OOF residuals `ε = y_OOF − μ̂_OOF` across the whole outer-training fold.
3. **Fold smearing factor.** `s_f = mean(exp(ε))` from that OOF pass.
4. **Refit.** Refit `StandardScaler`+`Ridge(α*)` on the **full** `T_f`.
5. **Predict.** Predict `μ̂` on `E_f`.
6. **Retransform.** Model variance forecast `h_model = s_f · exp(μ̂)`. The **LMP score** for `E_f` is `μ̂` itself (pre-retransformation), not `h_model`.

### 7.2 Log-space baselines (HAR, time_of_day)
For each log-space baseline `b`: produce OOF log forecasts over `T_f` via the same inner walk-forward (fit `b` on inner-`tr`, predict inner-`val`); `s_b = mean(exp(ε_b))`; fit `b` on full `T_f`; its variance forecast anywhere is `s_b · exp(μ̂_b)`. Persistence and EWMA are **unchanged** (variance-space; no `s`).

### 7.3 Baseline selection (unchanged mechanism)
Exactly v1's rule — best of the four candidates by **TRAIN QLIKE** on `T_f`, fixed for `E_f` — the ONLY difference being that log-space candidates' variance forecasts now carry their `s_b`.

### 7.4 Leakage guarantees (asserted in code/tests)
- `s_f`, `s_b` use **only** inner-validation (out-of-fold) residuals from `T_f`. Never in-sample fitted residuals; never any row of `E_f`.
- Final point forecasts (`μ̂`) come from a refit on full `T_f`; the multiplying factor is the OOF-estimated `s_f` (mildly conservative — the accepted trade-off).
- Determinism: Ridge closed-form; smearing a deterministic function of residuals; inner-split geometry, grid, `INNER_K`, embargo all fixed ⇒ reproducible, seed-independent.

### 7.5 Invariances (become tests)
- **Secondary metrics unchanged:** incremental log-RV R², MZ slope/intercept, log-RV MSE/MAE depend only on `μ̂` ⇒ identical to v1.
- **LMP diagnostic unchanged BY CONSTRUCTION:** the LMP score is `μ̂`, not the smeared forecast ⇒ AUC and reliability identical to v1.
- **Only QLIKE-space quantities move:** QLIKE, % reduction, per-year reductions, block-bootstrap significance.

## 8. Code touchpoints
- `config.py`: `RETRANSFORM="duan_smearing"`, `SMEARING_OOF=True`, `SMEARING_SCOPE="all_qlike"`, `INNER_K=5`.
- `model.py`: `select_alpha` scores α on OOF-smeared QLIKE; `smearing_factor` (dedicated OOF pass at α*); `fit_predict` returns `(μ̂_test, h_test=s_f·exp(μ̂_test), α*, s_f, scores)`.
- `baselines.py`: log-space baselines carry an OOF smearing factor; selection uses the retransformed variance forecasts; persistence/EWMA untouched.
- `phase1.py`: threads `s`; the model's LMP score is `μ̂` (not `h`); asserts §7.4.
- Results: `market_state/results/phase1_v2_mes_dev.txt`. v1 results/docs retained unchanged.

## 9. Freeze checklist (satisfied)
1. `SMEARING_SCOPE = "all_qlike"` — confirmed.
2. v2 pre-registration (§0–§6) and this specification (§7–§8) complete.
3. On freeze: docs persisted; §8 implemented; Phase-1 v2 run on **MES dev only**; hold-out stays locked.
