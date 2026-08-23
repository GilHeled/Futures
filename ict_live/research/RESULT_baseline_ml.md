# First baseline ML (shadow mode) — result record

**Status: EXPERIMENTAL, shadow-mode only. Nothing changed in the deterministic engine. Locked OOS
(≥2025-01-01) untouched.** Date: 2026-08-22.

## Data
- Source: raw Databento **5m** (cached), MES + MNQ, dev **2019-05 → 2024-12** (OOS ≥2025 sealed).
- Path: raw 5m → roll segmentation (state reset per contract) → resample → engine → features →
  outcomes → one row per distinct candidate. Signal TF **1H** (15m/others pending).
- Rule-#3 check: 100/100 overlapping 5m bars matched TradingView exactly (mid-contract date).

## Dataset audit (candidate-ranking set, MES+MNQ 1H)
- Examples (cleanly resolved, `target_before_stop` ∈ {0,1}): **10,050** (39% positive).
  Fixed-R target (`r2_before_stop`): 10,922 (25% positive).
- Chronological split (train_end 2023-07-01): train ~7,600 / val ~2,450. LOCKED OOS not generated.
- Even across years (~1,700–2,000/yr, 2019–2024); 48 contract segments (~150–330 each).
- Purge losses: ~300/instrument purged for roll windows (outcome horizon must fit in-segment);
  split-boundary straddlers purged (0 clean straddlers at this boundary).
- Feature missingness: `session` None on 7,444 (legitimate — candidate outside any killzone, encoded
  "none", not missing data); `dr_location_norm`/`dr_zone` None on 5 (no range yet). No NaN leakage.
- Leakage guard: hard assertion — no outcome/label key in the feature matrix (passes).

## Results (validation AUC)
| Configuration | LogReg | RF | Notes |
|---|---|---|---|
| `target_before_stop`, all features | 0.775 | **0.786** | **inflated** — dominated by `rr` |
| `target_before_stop`, no rr/rank (ICT structure only) | 0.628 | 0.638 | modest structural signal |
| `r2_before_stop` (fixed 2R, no distance confound) | 0.635 | 0.622 | cleaner target |
| `r2_before_stop`, no rr/rank | 0.614 | 0.568 | weak structural signal |

## Honest interpretation
- **The headline 0.79 AUC is a geometric near-tautology.** LogReg coefficient `rr = −6.19`: a higher
  reward:risk means the target is farther *in R units*, so "reached target before a 1R stop" is
  mechanically less likely. The model largely learns "closer targets get hit more often." Not ICT
  insight.
- **ICT structural features carry a modest-but-real edge**: AUC ~0.57–0.64 above the 0.5 random
  line, with the top structural predictors being **`sweep_rejection`** (how decisively the
  manipulation rejected), **`dr_location_norm`** (premium/discount location), **`sweep_rank`**,
  **`mss_rank`**, **`n_structural`** — all ICT-meaningful. Weak, not strong.
- **Shadow finding on the rules**: the engine's own `actionable` gate (RR≥3) scores **AUC 0.45**
  (below random) at predicting target-reaching and ~0.53 for fixed-2R — i.e. the RR≥3 filter selects
  setups *less* likely to reach target (the hit-rate vs RR tradeoff). `target_before_stop` alone is
  therefore the wrong objective; **expectancy needs hit-rate AND R together**.

## Conclusion / next
The dataset + shadow-ML pipeline are validated end-to-end (causal, leakage-guarded, chronological,
purged). ICT structure has a weak but genuine outcome signal; the naive win-rate target is
geometry-confounded. Recommended next: (1) an **expected-R regression** target (Model B proper) that
combines hit-rate and R, rather than a binary win flag; (2) the **manipulation-ranking vs human**
task (Model A) once human annotations exist; (3) more data (15m TF, MYM/M2K) before any conclusion;
(4) OOS stays sealed until a model is frozen. No rule was tuned from these results.
