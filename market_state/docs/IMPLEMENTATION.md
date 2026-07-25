# IMPLEMENTATION DOCUMENT — Target A (MES)

> **Status: APPROVED & LOCKED — 2026-07-24.** Per §3 of the frozen
> pre-registration, this document defines the two things deliberately left out of
> the scientific contract: **(1) the single prediction model** and **(2) the
> concrete feature list within the frozen families**. Both are now fixed;
> changing either requires opening a new pre-registration (no model shopping).
> α-selection is the nested time-series CV in §1.1 (approved as specified — a
> single-scalar hyperparameter fit, not a model search).

## 1. Prediction model (single, fixed specification)
**Ridge (L2-regularized) linear regression of `log(RV)` on standardized features.**
- One model class — **no comparison across model classes, feature sets, or preprocessing** under this study (§3 rule). The only quantity fit from data beyond the linear coefficients is the single regularization scalar `α`, chosen by the fixed nested procedure below.
- Standardization: features centered/scaled using **training-fold statistics only** (scaler refit per outer fold; the outer-test fold is never used to fit the scaler).

### 1.1 α-selection — exact, deterministic, train-only (no hidden model search)
Within each **outer** walk-forward fold, α is selected using ONLY that fold's training window; the outer-test fold is never touched during selection.
- **Fixed grid, pre-declared:** `α ∈ {0.01, 0.1, 1.0, 10.0, 100.0}` — nothing outside this grid, no continuous optimizer.
- **Inner CV:** 5 expanding, time-ordered inner folds *inside* the outer-train window, with the **same purge + 12-bar embargo** at inner boundaries, so α-selection is itself leakage-safe.
- **Selection criterion (fixed):** minimize **mean inner-validation QLIKE** (the study's primary loss, on `h = exp(inner log-RV forecast)`). The criterion is fixed in advance and cannot be swapped.
- **Refit:** the winning α is refit once on the full outer-train window, then applied to the outer-test fold.
- **Determinism:** Ridge is closed-form; grid, inner-K (=5), criterion, purge/embargo, and feature set are all fixed ⇒ fully reproducible, no seed dependence, no post-hoc choice. Tuning one continuous regularization scalar inside a single estimator is an internal hyperparameter fit, NOT a model search.
- Rationale: interpretable, low-variance, HAR-consistent (linear on log realized variance); the simplest model that combines the feature families additively. A nonlinear model (trees, nets) is out of scope and would require a new pre-registration.

## 2. Concrete features (within the frozen families of §3; all causal, no signed direction)
| # | Feature | Family | Definition (at forecast bar t) |
|---|---------|--------|-------------------------------|
| 1 | `log_rv_lag6` | RV history | log trailing realized variance, last 6 bars (30 min) |
| 2 | `log_rv_lag24` | RV history | log trailing realized variance, last 24 bars (~2 h) |
| 3 | `log_rv_session` | RV history | log realized variance since RTH open today |
| 4 | `log_rv_prev_session` | RV history | log prior-session total realized variance |
| 5 | `atr_regime` | Vol regime & range | ATR(14) / median(ATR over trailing 20 sessions) |
| 6 | `or_width` | Vol regime & range | (OR_high − OR_low) / ATR (09:30–10:00 range) |
| 7 | `session_phase` | Time-of-day | fraction of RTH elapsed, 0..1 |
| 8 | `participation` | Participation/volume | bar volume / median(volume at same time-of-day, 20 sessions) |
| 9 | `gap_abs` | Overnight-gap magnitude | \|overnight gap\| / ATR (unsigned) |
| 10 | `efficiency_ratio` | Persistence/efficiency | Kaufman efficiency ratio over last 12 bars (direction-free magnitude of net move / summed absolute moves) |

All six frozen families are represented and no feature falls outside them. All are magnitude/activity quantities; **none encodes the sign of returns** — `gap_abs` takes the absolute value and Kaufman ER's numerator is the absolute net change, so both are pure magnitude/persistence measures. Features are dropped to NaN where not yet computable and their rows are excluded (they never overlap the eligible sample window in practice).

## 3. Forecast → variance mapping (frozen convention, applied identically to all log-space forecasters)
The model and the log-space baselines (HAR, time-of-day) produce a **log-RV** forecast. The variance forecast used in QLIKE is **`h = exp(log-RV forecast)`** for *all* of them (persistence and EWMA are already in variance space). Using the identical mapping for model and baselines keeps the QLIKE comparison apples-to-apples; no per-forecaster bias correction is applied. This convention is fixed now so it cannot be chosen to favor the model.

## 4. What this document does NOT do
- It does not alter the horizon, label, eligibility rules, baselines, metrics, walk-forward protocol, Go/No-Go, or stopping rules — all frozen in the pre-registration.
- It does not select among models or feature sets using any result.
- It does not touch the locked hold-out.

## 5. On approval
Once approved, `market_state/features.py` implements exactly the table in §2, and the walk-forward modeling run (Phase 1) proceeds on **MES dev only**, evaluating the single Ridge model against the pre-registered baselines under the frozen protocol.
