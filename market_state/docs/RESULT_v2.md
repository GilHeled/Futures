# RESULT — Target A: Expected Realized Volatility (MES)

**Status: VALIDATED (v2). Research line COMPLETE — 2026-07-24.**
This is the durable final record. The v1 and v2 pre-registrations and
implementation specifications are preserved unchanged alongside it.

---

## 1. Question
Is forward **30-minute intraday realized volatility** on MES predictable **beyond a strong volatility baseline**, using causal, direction-free activity/volatility features? (A market-*context* claim about second moments — explicitly not a directional or trading claim.)

## 2. Outcome (one line)
**Yes — modestly but significantly.** Under a pre-registered protocol, a single Ridge model beat the strongest pre-registered baseline (HAR) out-of-sample on the locked hold-out: **+2.30% QLIKE reduction, block-bootstrap P(improvement≤0)=0.0006.**

## 3. How we got here (two frozen protocols)
- **v1 (frozen, then NO-GO).** The pipeline modelled `log(RV)` and mapped to a variance forecast as `h = exp(μ̂)`. On dev this failed the primary QLIKE endpoint (−7.85% vs the selected EWMA baseline; 0/5 years; bootstrap P=1.0). A read-only diagnostic traced the failure to a **foreseeable implementation-design error**: `exp(μ̂)` is the conditional *geometric* mean, whereas a QLIKE-on-RV endpoint requires the conditional *arithmetic* mean `E[RV|X]` (the textbook log-linear retransformation bias). v1 stands **as a formal NO-GO** and is not reinterpreted.
- **v2 (frozen, minimal correction, terminal).** The single change: replace `h=exp(μ̂)` with a pre-declared **Duan (1983) smearing** retransformation `h = s·exp(μ̂)`, `s` estimated from **leakage-safe out-of-fold development residuals**, applied symmetrically to every log-space forecaster (model, HAR, time-of-day); persistence/EWMA unchanged (variance-space). Scope `all_qlike` (also inside α-selection); selection and calibration kept separate. Everything else identical to v1. **v2 was declared terminal — no v3.** The second-look on the same dev data was disclosed in the pre-registration.

The correction was legitimate (not a rescue) because it is (a) mandated by the endpoint from first principles, (b) fully specifiable from training data before any run, (c) symmetric across all log-space forecasters, and (d) limited to fixing a known implementation error.

## 4. Evidence

### 4.1 Development (MES, 2019-05 → 2024-12; 6 purged annual folds; 55,239 obs)
All seven pre-registered Go/No-Go criteria PASS. Selected baseline = **HAR (smeared)** every fold (a *harder* benchmark than v1's EWMA).
- QLIKE reduction vs baseline **+6.09%** (≥5.0% margin); bootstrap **P=0.0000**; incremental log-RV R² +0.075; MZ slope 0.954; **years positive 5/5**; drop-best-year +4.91%; beats time-of-day +70%.

### 4.2 Development robustness (§12, confirmation-only, no methodological change)
- **Garman–Klass alternative RV proxy:** +7.58% reduction, P=0.0000, 5/5 years, drop-best +5.82% — edge persists under a different RV estimator.
- **Regime terciles (trailing-session RV):** +5.00% (low) / +7.13% (mid) / +6.43% (high) — positive in every regime.

### 4.3 Hold-out confirmation (SINGLE, FINAL; 2025-01-01 → 2026-07-09; 17,901 obs / 384 days)
Trained on full dev with identical frozen procedures; evaluated **once**.
- Raw QLIKE: model **0.453846**, selected baseline HAR **0.464520**, time-of-day 1.156810.
- **QLIKE reduction vs baseline = +2.30%**; block-bootstrap (5-day blocks, 5000 resamples, seed 20260724) **P(improvement≤0)=0.0006**, 95% CI [7.21e-03, 2.54e-02], n_days=384.
- MZ slope **1.0025**, intercept −0.077 (near-ideal OOS calibration — the level bias is corrected). Incremental log-RV R² +0.0076; +60.8% vs time-of-day.
- Annual: 2025 +1.35%, 2026 +4.17%. Monthly: **14/19 months positive, 5 negative** (edge is small and noisy at monthly resolution; significance comes from the day-level aggregate, not from every month).
- Smearing factors (OOF on dev only): model 1.5230, HAR 1.5575, time-of-day 2.4038.
- **Dev-only calibration guarantee (asserted in code):** latest calibration timestamp 2024-12-31 20:25 UTC ≤ dev end; hold-out scored 2025-01-02 → 2026-07-08; the model α, s_f, and every baseline s_b were estimated on dev rows only — no hold-out observation entered α-selection, smearing, or baseline fitting.

**Hold-out gate (§6/§13: reduction > 0 AND bootstrap P ≤ 0.05): PASS → GO.**

## 5. Conclusion and honest caveats
- **Validated:** a **modest but statistically supported** improvement in forecasting 30-minute MES realized volatility beyond the selected HAR baseline, confirmed out-of-sample.
- **It is a volatility-forecasting model — NOT a directional or trading signal**, and NOT evidence that a profitable trade exists. This study did not evaluate trading performance (§15–§16). Any use is as a market-context / risk-management input (sizing, stop-width, engage/stand-aside, style selection).
- **The effect shrank out-of-sample** (dev +6.09% → hold-out +2.30%) while remaining significant — a plausible, reassuring generalization pattern, but the realized edge is small.
- **The LMP (Large-Move-Probability) line is NOT supported:** the diagnostic AUC stayed ≈0.50 throughout (hold-out 0.5011). Per §10/§14, no separate LMP pre-registration is opened.
- **Direction remains unaddressed** (and was previously found null in the closed intraday-direction study).

## 6. Reproducibility & provenance
- Code: `market_state/` (`config, data, labels, features, baselines, model, metrics, bootstrap, purged_cv, phase1, phase1_robustness, holdout`). Tests: `tests/market_state/` (47 passing). Deterministic (Ridge closed-form; fixed bootstrap seed); the hold-out run reproduces byte-for-byte.
- Frozen docs (preserved unchanged): `PRE_REGISTRATION_TARGET_A.md` (v1) + `IMPLEMENTATION.md`; `PRE_REGISTRATION_TARGET_A_v2.md` + `IMPLEMENTATION_TARGET_A_v2.md`.
- Results: `results/phase1_mes_dev.txt` (v1 NO-GO), `results/phase1_v2_mes_dev.txt` (v2 dev GO), `results/phase1_v2_robustness_mes_dev.txt`, `results/holdout_v2_mes.txt` (one-shot), `results/holdout_v2_mes_full.txt` (enriched detail).

## 7. Status
**Research line complete.** Target A is validated as a volatility-forecasting result under this study. v2 is terminal; no further analysis of this development or hold-out data will be undertaken. Any future work (e.g. other instruments, a trading application, or a fresh directional hypothesis) would be a new, separately pre-registered study.
