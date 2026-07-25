# PRE-REGISTRATION v2 — Target A: Expected Realized Volatility (MES)

> **Status: FROZEN — 2026-07-24.** A NEW frozen protocol. It does **not**
> reinterpret v1. Machine-readable constants: `market_state/config.py`. The
> exact retransformation algorithm: `IMPLEMENTATION_TARGET_A_v2.md`. Changing
> any item here terminates this study.

## 0. Relationship to v1 (status of the prior run)
- **v1 remains a formal NO-GO on the record.** Under the v1 protocol the pre-registered primary endpoint (QLIKE on RV) failed decisively (−7.85% vs the selected EWMA baseline; 0/5 years positive; bootstrap P=1.0). That verdict stands and is **not** revised.
- **v1 is not a demonstrated null either.** The failure was traced to a foreseeable **implementation-design error**: v1 mapped the log-space forecast to a variance forecast as `h = exp(μ̂)`, the conditional *geometric* mean, whereas a QLIKE-on-RV endpoint requires the conditional *arithmetic* mean `E[RV|X]`. The gap is the textbook log-linear **retransformation bias** — specifiable in advance, estimable from training data, independent of any observed result.
- **v2 is a minimal correction, not a new hypothesis, model, or feature set.** The ONLY change from v1 is the log→variance retransformation. Everything else — label, features, folds, baselines, metrics, calibration/stability requirements, Go/No-Go thresholds, stopping rules, locked hold-out — is **identical to v1**.
- **v2 is TERMINAL. There will be no v3.** This correction is justified solely because it fixes a foreseeable implementation-design error that should have been caught before the first run. If v2 fails its Go/No-Go, **the research line closes** — no further refinement, retransformation variant, feature change, model change, or any other adjustment will be tried on this development data. v2 must not become an open-ended sequence of refinements. (This is the whole justification for permitting a second frozen protocol at all; it evaporates the moment v2 becomes a springboard for v3.)

## 1. The single change (the entire delta from v1)
v1 froze `h = exp(μ̂)` with "no bias correction." **v2 replaces this with a pre-declared Duan (1983) smearing retransformation**, estimated per outer fold from **leakage-safe out-of-fold residuals** within that fold's training period:
```
    h(X) = s · exp(μ̂(X)) ,   s = mean_over_OOF_train_residuals( exp(ε) ) ,   ε = log(RV) − μ̂_OOF
```
- `s` is a **single positive scalar per outer fold per log-space forecaster**.
- `s` is estimated **only** from out-of-fold residuals produced by an inner purged+embargoed walk-forward *inside that outer fold's training period*. **Never** from in-sample fitted residuals and **never** from any outer-test observation.
- One method only. **No comparison** among lognormal correction, smearing, Gamma GLM, or any alternative. Smearing is the frozen choice.
- **Persistence and EWMA are unchanged** — they already forecast in variance space (arithmetic), so no retransformation is applied to them.

Rationale for smearing: distribution-free, a determined training-estimable quantity (not a tunable knob), applied identically to every log-space forecaster (model, HAR, time-of-day) — so it cannot selectively favor the model.

## 2. Retransformation scope (frozen): all QLIKE decisions
Applied **everywhere a QLIKE-based decision is made** — the final outer-test forecast AND α-selection's inner-validation QLIKE (each candidate α scored on its own OOF-smeared forecast). Config: `SMEARING_SCOPE = "all_qlike"`. Rationale: selecting α under one objective and evaluating under another would be internally inconsistent; this tunes α on exactly the estimator it is judged on, with no added researcher degree of freedom.

## 3. What is UNCHANGED from v1 (frozen)
Hypothesis, 30-min horizon, forward RV `Σr²` with target `log(RV)`, eligibility 10:00–15:25 ET, incomplete/cross-session windows dropped; the 10 concrete features within the 6 frozen families; the single Ridge model with nested-CV α over {0.01,0.1,1,10,100}; baselines {persistence, EWMA, HAR, time-of-day} and the train-only selection rule (must also beat time-of-day); 6 annual purged folds, embargo 12; dev 2019-05→2024-12, hold-out 2025-01-01→2026-07-09 LOCKED; primary QLIKE (% reduction), secondary MZ-R²/MSE/MAE + MZ regression; Go/No-Go thresholds (≥5.0% reduction, bootstrap P≤5%, incremental log-RV R²>0, MZ slope∈[0.9,1.1], ≥4/6 years + drop-best-year, beats time-of-day); the block bootstrap; stopping rules; the LMP diagnostic and its interpretation caveats.
**LMP score = the log point forecast `μ̂` (before retransformation), unchanged from v1** ⇒ the model's LMP AUC and reliability are identical to v1. Smearing applies **only** to the RV forecasts used for QLIKE, never to the LMP score (because `s_f` varies by fold, pooled ranks of the smeared forecast are not guaranteed invariant).

## 4. Endpoint discipline
Primary endpoint unchanged: **QLIKE on RV.** v2 does not weaken it or promote the secondary log-space metrics. A pass requires clearing the *same* QLIKE Go/No-Go v1 failed — now with a correctly calibrated variance forecast.

## 5. Second-look / multiple-testing disclosure
This is the **second frozen protocol on the same MES development data**, which elevates family-wise error risk. Mitigations, stated plainly: v1↔v2 differ **only** by a deterministic, theory-mandated retransformation (no new hypothesis/model/features/horizon/swept parameter); the correction is outcome-independent and symmetric across all log-space forecasters; no rescue searching (v2 is terminal — no v3, §0); and the **locked hold-out remains the ultimate guard**, touched exactly once and only if v2 dev passes. Recorded so the second-look is transparent.

## 6. Go / No-Go (identical thresholds to v1)
**A(v2) PASSES (dev)** iff ALL hold, with QLIKE on the smeared variance forecast: reduction vs selected baseline ≥ 5.0%; block-bootstrap P(improvement≤0) ≤ 5%; positive incremental log-RV R² (unchanged from v1, since smearing does not alter `μ̂`); MZ slope ∈ [0.9,1.1]; ≥4/6 years positive + drop-best-year positive; beats time-of-day. Then confirm ONCE on the locked hold-out ⇒ GO. Otherwise NO-GO ⇒ **the research line closes (no v3, §0)**.
