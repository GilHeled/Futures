# PRE-REGISTRATION — Target A: Expected Realized Volatility (MES)

> **Committed, human-readable freeze.** Machine-readable numeric source of truth:
> `market_state/config.py`. Changing any item here or in `config.py` terminates
> this study and requires registering a new one. The prediction model and the
> concrete feature list are NOT frozen here — they are defined in
> `market_state/docs/IMPLEMENTATION.md`, approved before development, and then
> locked (see §3).

**Status: FROZEN (final form) — 2026-07-24.** No element below may be modified except by intentionally starting a new research program; corrections of that kind require a new pre-registration, not an edit here. Scope of the study: MES development data only; no FX pull; no LMP-threshold optimization; no T3/T4 directional-structure targets. The prediction model is intentionally out of scope and is defined separately in the implementation document (see §3). The research-and-design reasoning behind this is recorded in memory (`project_market_state_prediction.md`).

## 0. Hypothesis (one sentence)
Forward intraday realized volatility on MES is predictable **beyond a strong volatility baseline** from causal activity/volatility features — a market-context claim, not a directional one.

## 1. Forecast horizon
- **Single, fixed horizon: the next 30 minutes = 6 consecutive 5-minute bars.** No horizon search.
- **Decision times:** at the close of each 5-min bar from **10:00 to 15:25 ET** (OR fully formed by 10:00; the full 6-bar forward window must lie within the RTH session, ending by 16:00 ET). One forecast per eligible bar.

## 2. Realized-volatility label (exact)
- Forward **realized variance**: `RV_t = Σ_{i=1..6} r_{t+i}²`, where `r = ln(close/close_prev)` over the 6 forward 5-min bars (all intraday → no overnight returns enter the window).
- **Model target = `log(RV_t)`** (log stabilizes variance; standard practice). Realized quantity for scoring is `RV_t` itself.
- Bars with an incomplete/gapped forward window are excluded (never imputed).

## 3. Eligible input data & feature families (frozen; all strictly causal)
MES cached 5-min bars, dev period only. **Frozen feature FAMILIES** (the concrete individual features are specified in the implementation document, but must stay *within* these families; all strictly causal, activity/volatility-oriented, and containing **no signed-direction inputs**):
- **Realized-volatility history (HAR-style)** — trailing realized variance at multiple look-backs (e.g., last 30 min / last hour / session-to-date / prior session).
- **Volatility regime & range** — ATR-based regime measures; opening-range width.
- **Time-of-day / seasonality** — session-phase / time-of-day encodings.
- **Participation / volume** — volume relative to a time-of-day norm.
- **Overnight-gap magnitude** — |gap| (unsigned).
- **Persistence / efficiency** — trend-vs-chop measures (e.g., efficiency ratio, variance ratio).
- **The prediction MODEL is deliberately NOT part of this pre-registration.** This pre-registration freezes only the scientific claim and its test — the hypothesis, labels, data-eligibility rules, feature families, validation protocol, baselines, evaluation metrics, Go/No-Go criteria, and stopping rules. It does **not** specify the prediction model.
- **The model is fixed in a separate implementation document, approved before development begins.** That document defines a single prediction model with a fixed specification. **Once the implementation document is approved, the model is fixed and cannot be changed without opening a new pre-registration.** This prevents model shopping (trying several models and keeping the best) while keeping this document focused on the research question. A model defined this way cannot alter any frozen item above; conversely, any change to a frozen item here requires re-registration.

## 4. Candidate baselines & selection rule
- Candidates: **(i) persistence** (forecast = `rv_lag30`); **(ii) EWMA** of RV; **(iii) HAR-RV** (regression on prior 30-min / 1-hour / prior-session RV components); **(iv) time-of-day climatology** (training mean `log(RV)` for that time-of-day).
- **Selection rule (no model-favoring):** the operative benchmark per fold is **the best of the four candidates on TRAINING data only** (by QLIKE). **Once selected on a fold's training data, that baseline is fixed for the corresponding test fold and may not be re-selected, swapped, or re-ranked using any test-fold result** — the choice is made strictly before the test fold is scored. The model must beat *that* fixed baseline out-of-sample. Additionally, the model must **beat the time-of-day climatology** specifically (guarantees the edge is not merely intraday seasonality).

## 5. Walk-forward, purge, embargo
- **Purged + embargoed expanding walk-forward**, 6 annual folds over the dev period; fold 0 is training-only.
- **Purge:** drop any training sample whose 6-bar forward label window overlaps (or falls within the embargo of) the test fold.
- **Embargo = 12 bars** (2× the label horizon) at fold boundaries.

## 6. Locked development & hold-out periods
- **Development: 2019-05 → 2024-12 (MES).** All modeling, baseline selection, and walk-forward here only.
- **Hold-out: 2025-01-01 → 2026-07-09 — LOCKED, untouched until the single final evaluation** (enforced in code by the hold-out guard).

## 7. Primary & secondary evaluation metrics
- **Primary: QLIKE** (`RV/h − ln(RV/h) − 1`, h=forecast variance; robust to vol-proxy noise), reported as **% QLIKE reduction vs. the selected baseline**, OOS.
- **Secondary:** out-of-sample **Mincer–Zarnowitz R²** of `log(RV)`; MSE/MAE of `log(RV)`; the MZ regression (realized on forecast) for unbiasedness.

## 8. Minimum improvement over baseline
The improvement over the selected baseline must clear both a statistical gate and a practical-significance gate:
- **(binding, frozen now) Statistical significance:** block-bootstrap **P(QLIKE improvement ≤ 0) ≤ 5%** (day-level, 5-day blocks, 5000 resamples), **AND** positive incremental out-of-sample log-RV R² over the selected baseline. Together with §9 (calibration + temporal stability) this is the *scientific* gate and is fully frozen now.
- **(practical-significance margin — FINALIZED, frozen now): a minimum relative QLIKE reduction of 5.0% vs. the selected baseline**, OOS, aggregated over the dev walk-forward, to prevent celebrating a statistically-significant-but-economically-trivial gain. **Rationale for 5%:** in the realized-volatility literature, forecast-loss improvements over a strong HAR/persistence baseline are typically single-digit to low-double-digit percent; 5% is chosen as a conservative floor that is (a) comfortably above noise-level differences between forecasts, (b) demanding of a genuinely non-trivial improvement rather than a marginal one, and (c) not so high as to reject a real-but-modest edge over an already-strong baseline. It is a *practical* floor layered on top of — never a substitute for — the binding statistical-significance gate and the §9 calibration/stability gates. The value is fixed now, before any implementation or data touch, precisely so it cannot be adjusted after seeing results. **This is not a citation-grade constant but a pre-committed, conservatively reasoned judgment; it is locked and will not be revisited absent a decision to start a new research program.**
- **Single freeze:** with this value fixed, **every element of the pre-registration is now locked.** There is one freeze moment (this document, in its final form); the research design is not to be revisited unless we intentionally begin a new research program.

## 9. Calibration & temporal-stability requirements
- **Calibration/unbiasedness:** Mincer–Zarnowitz slope within **[0.9, 1.1]** and intercept ≈ 0; decile reliability (mean realized ≈ mean forecast per forecast-decile).
- **Temporal stability:** QLIKE improvement positive in **≥ 4 of 6 dev years** AND **survives drop-best-year** (mean-of-year improvement stays positive). Inference via **day-level block bootstrap** (5-day blocks, 5000 resamples).

## 10. Frozen Large-Move-Probability (LMP) threshold-crossing diagnostic
- **Frozen threshold (NOT optimized): LMP event = `max(H−ref, ref−L)` over the next 6 bars ≥ 2.0·ATR_t** (direction-agnostic). One threshold; no sweep.
- **Basis for the 2.0·ATR value (documented — there is no citation-grade basis, so the choice is justified rather than cited):** (a) it is an *a-priori*, round, interpretable scale that is clearly larger than a single 5-min bar's typical range, so the event denotes a *meaningful* move rather than noise, while remaining common enough over a 30-min window to be non-degenerate (neither near-0% nor near-100% base rate); (b) **the diagnostic's primary metric is AUC, which ranks the forecast across *all* thresholds and is therefore largely insensitive to the exact cut** — the 2.0·ATR value only fixes the specific event used for the human-readable reliability table, not the headline conclusion; (c) it is frozen precisely so it cannot be tuned toward a favorable result. It is a diagnostic, not a parameter to be optimized.
- **Diagnostic (report-only, no new fit, no tuning):** use the A model's continuous forecast as a *score* for the LMP event and report **AUC** and **decile reliability** of the realized LMP rate vs. forecast, compared with the baselines' AUC. Purpose: gauge whether A's information would support a later LMP product. **This is diagnostic only — it is not a second optimized target and does not by itself gate A.**

## 11. Multiple-testing controls
- By construction, essentially **one primary test**: one instrument, one horizon, one label, one frozen feature set, one baseline-selection rule, one frozen LMP threshold, hold-out evaluated **exactly once**. No scanning over M, N, features, or model classes. The single block-bootstrap significance test carries the multiple-comparison burden (there are effectively no other comparisons to correct for).

## 12. Robustness checks (reported alongside; primary Go/No-Go rests on §8–9)
- Per-year and drop-best-year (as above).
- **Regime stability:** report the improvement stratified across **predefined volatility regimes** (e.g., terciles of trailing-session realized volatility) and across **individual years** — no special-case single-year exclusion. The improvement should hold in sign across regimes and be broadly stable across years (per §9).
- **Alternative RV proxy:** repeat with a Garman–Klass range estimator as the label; improvement should persist in sign.
- **Time-of-day control:** improvement survives beating the time-of-day climatology (see §4).

## 13. Go / No-Go
- **A PASSES (dev)** iff ALL hold: QLIKE reduction vs the selected baseline **meets or exceeds the practical-significance margin fixed in §8 (≥5.0% relative)**; P(improvement≤0) ≤ 5%; positive incremental log-RV R²; MZ slope ∈ [0.9,1.1]; ≥4/6 years positive + drop-best-year positive; beats time-of-day climatology. *(The single source of truth for the margin is §8; the value is restated here only because it is now locked.)*
- **Confirmation:** if dev passes, evaluate **once** on the locked hold-out; QLIKE reduction must remain positive and block-bootstrap-significant. **GO** only if hold-out confirms.
- Otherwise **NO-GO**.

## 14. Stopping rules
- **If A fails dev Go/No-Go AND the LMP diagnostic is unpromising (AUC not materially above baseline) → close this research line** with a documented null (no hold-out beyond the single confirmation).
- **If A fails but the LMP diagnostic is strong** (the tail-independence exception) → document it; a *separate* LMP pre-registration may be considered, but **this A line stops** either way.
- **If A passes dev but fails hold-out** → not validated (regime/overfit); document; stop.
- **If A passes and the LMP diagnostic is promising → open a separate LMP pre-registration** (the binary context product).
- No rescue searching, no parameter/horizon/threshold sweeping, no model-class search under this pre-registration.

## 15. Practical interpretation of success (required)
A validated Target A is a **market-context and risk-management input**: it estimates *how much* the market is likely to move over the next 30 minutes, informing whether the period is worth monitoring, how to size, how wide to set stops, and which style to favor. **It is explicitly NOT a directional signal, NOT an entry signal, and NOT evidence that a profitable trade can be captured.** Predicting volatility well says nothing about *which way* price goes or *whether* an edge is realizable — those remain unaddressed (and, for direction, already shown absent). Success here is a better-informed *environment* read for a human trader, nothing more.

## 16. Economic interpretation (how a discretionary trader would actually use a successful forecast)
Defined *before* implementation, so "success" has a concrete operational meaning rather than a purely statistical one. A validated, well-calibrated 30-min RV forecast would enter a discretionary MES trader's workflow in these ways — all **context and risk inputs, applied only once a direction/entry decision has been made by other means**:
- **Position sizing / risk budgeting.** Scale intended size *inversely* to forecast volatility so that dollar risk per trade is roughly constant across regimes (a high-RV forecast → smaller size; low-RV → larger), rather than sizing by a fixed contract count.
- **Stop and target width.** Set stop distance and profit target as multiples of *expected* forward volatility rather than trailing/static ATR, so stops are wide enough to survive expected noise in a high-vol window and tight enough to be meaningful in a quiet one.
- **Engage / stand-aside timing.** When the forecast flags an unusually quiet 30-min window, a breakout- or momentum-style trader can *stand aside* (low expected follow-through, high chop risk); when it flags an unusually active window, that trader can *raise attention*. This is a "when to look," not a "which way to trade."
- **Style selection.** Bias toward mean-reversion tactics in forecast-low-vol windows and toward trend/breakout tactics in forecast-high-vol windows — the forecast informs *which playbook*, never the trade signal itself.
- **Session/day risk filtering.** Aggregate the forecast into a session-level activity read to decide whether the day merits full engagement or reduced exposure (relevant under funded-account drawdown limits).

**Boundary (restated so it cannot be misread):** none of the above is a directional or entry signal, and a good RV forecast is *not* evidence that any profitable trade exists. It changes *how much* and *how* a trader who already has an entry thesis acts — not *whether* the thesis is right or *which* direction to take. If Target A validates, its only claim is that the *environment* is more predictable than a naive baseline says; converting that into money still requires a separate, independently-validated edge.

**Scope of this study (explicit):** This study does not evaluate whether these decisions improve trading performance. It evaluates only whether the information required to support such decisions is predictable beyond strong baselines.
