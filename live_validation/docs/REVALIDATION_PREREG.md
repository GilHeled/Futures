# PRE-REGISTRATION — Overnight Edge Re-Validation (single authoritative implementation)

> **Status: PHASE A FROZEN — 2026-07-26.** Only Phase A (the causality / optimism
> audit, §2) is frozen and runs now. The statistical protocol (§3–§9) is
> DELIBERATELY NOT frozen: it will be finalized *on the verified implementation*
> only after Phase A concludes the code is causally correct — because if Phase A
> finds a bug, the implementation changes and any pre-committed split/hypothesis
> would be meaningless. Offline only. No spending, no live, no trading.
>
> **OUTCOME (2026-07-26): Phase A concluded FAIL — a look-ahead leak was found and the
> overnight project was CLOSED. §3–§9 were never frozen or run. See
> `PHASE_A_RESULT.md` and `RESULT_OVERNIGHT_CLOSED.md`.**

## 0. Objective
Establish **one** authoritative implementation of the overnight strategy — the sequential `ShadowBook` (the exact code that would trade live) — and put **it** through the full rigor stack. The aim is NOT to reproduce the old off-hours research numbers (those scripts are lost; that measurement is retired to *historical evidence only*). If the sequential implementation shows a statistically significant, robust, net-of-cost edge, it **supersedes** the prior research and becomes the sole reference. If not, we **close the overnight project** exactly as we closed the volatility-monetization line.

## 1. The strategy of record (frozen, versioned)
The **sequential one-position `ShadowBook`** (`live_validation/shadow_book.py`) driven by `inference.compute_frame` + a frozen bundle: off-hours EV entry at 5-min close, 1.5·ATR stop, gap-aware dynamic-EV exit, one position at a time, held through the session. Every number below is produced by *this* code path. Its code-provenance hash + bundle meta are recorded with every result. No competing definition (matched-entry, walk-forward off-hours diagnostics) is used for validation again.

## 2. Phase A — Look-ahead / optimism audit (PREREQUISITE, gating)
Because the current-code reproduction is *stronger* than the retired research (MES ~2.7× at +1.28 vs +0.47 @3t), we must rule out that the harness is optimistic **before** trusting any edge.
- **Static audit** of `inference.py` + `shadow_book.py` for future-information use: feature causality (`build_feature_matrix`), the rising-edge signal calendar/debounce, the EV + class-return vector, session flags, entry (bar close), stop (gap-aware current bar), and EV-reversal exit (next-bar open).
- **Independent causal recompute:** re-derive entry/exit/PnL for a random sample of trades with a deliberately naive, prefix-only recomputation and reconcile to the `ShadowBook` to the cent.
- **Label-leakage check:** confirm the training labels/`class_return_vec` are strictly backward-estimated (no forward window bleeding into the frozen fit).
- **Gate:** if any look-ahead/optimism is found, it is fixed first (which may change the edge) and Phase B runs on the corrected code. If none is found, that itself is recorded as the explanation for "stronger than the retired matched-entry measurement" (sequential one-position ≠ overlapping matched-entry). **Phase B does not start until Phase A is clean.**
  - **Pure engineering verification only — no hypotheses, no statistics.** Phase A asks solely whether the implementation is causally correct; it does not evaluate the edge.

---
**NOT YET FROZEN (below).** §3-§9 are the intended shape of the statistical validation, to be finalized and frozen against the Phase-A-verified implementation. Splits, benchmarks, and Go/No-Go may change if Phase A changes the code.

---

## 3. Phase B — data splits (frozen model, genuine OOS + locked hold-out)
- **Frozen research bundle:** re-fit and **SAVE** (versioned, in `bundles/research/`) a frozen model per instrument trained to **2022-12-31** — the artifact-of-validation, distinct from any train-to-now live bundle. Fixes the provenance hazard (validated artifact == on-disk artifact).
- **Development (OOS):** 2023-01-01 → 2025-06-30. All Go/No-Go evaluation here.
- **Locked hold-out:** 2025-07-01 → 2026-07-09. Untouched until the single final confirmation, only if dev passes.
- **Instruments:** MYM, M2K, MES (MNQ already dropped). **[confirm]**

## 4. Metric, costs, fills
- **Primary:** net-of-cost avg R per trade on the sequential `ShadowBook`, **gap-aware fills, 3-tick per-side slippage** (report 5t as robustness); $1.50 round-trip commission.
- Reported: trade count, total R, per-year R, hit rate, exit-reason mix, max drawdown in R.

## 5. Null benchmarks (same sequential engine)
Both run through the **identical one-position sequential logic** (same entry calendar/timing/holding, only the direction rule changes):
- **Overnight-hold** (passive long on each entry).
- **Random-direction ensemble** (50 seeds, matched count/holding).
The model must **beat both** — positive paired/relative edge, not merely positive on its own.

## 6. Significance
**Weekly block bootstrap** (ISO week, 4-week blocks, 5000 resamples, fixed seed) on the per-trade net R aggregated to weeks; `P(mean ≤ 0) ≤ 0.05`. (Weekly blocks respect overnight serial structure; same tool used earlier in the project.)

## 7. Temporal robustness
Per-year net R **positive in a majority of dev years**, AND **drop-best-window** (8 equal-time windows) stays positive — at **both 3t and 5t**.

## 8. Go / No-Go
**Per instrument, dev PASS iff ALL:** net avg R > 0 with block-bootstrap `P ≤ 0.05`; beats overnight-hold AND the random ensemble; per-year majority positive + drop-best-window positive (3t and 5t).
**Evidence-based decision (NOT a vote):** each instrument is evaluated *independently* under the full rigor stack; any instrument that passes is a **candidate**. The decision to proceed rests on the **overall weight of evidence** — effect size, robustness, and economic significance — not on how many instruments passed. If all three fail, the overnight project closes. If one or more survive with convincing evidence, we decide whether that evidence justifies spending the single locked hold-out.
**Hold-out confirmation (once):** the surviving candidate(s), evaluated on the locked hold-out, remain net-positive and block-bootstrap-significant.

## 9. Supersede-or-close
- **GO** (≥2/3 dev pass AND hold-out confirms): the sequential `ShadowBook` **supersedes** the retired research and becomes the strategy of record; the chain of evidence is restored; we then revisit execution validation (Step 1b vs live paper). A permanent `RESULT_REVALIDATION.md` records it.
- **NO-GO:** the overnight project is **closed** (documented null), like the vol-monetization line. No live, no spend.
- No parameter tuning, no strategy changes, no instrument additions to rescue a fail. One implementation, one record, one chain.

## 10. Deliverables
Versioned research bundles (`bundles/research/`); `live_validation/revalidation.py` (dev + hold-out on the sequential book, nulls, bootstrap, temporal); `results/revalidation_*.txt`; `docs/RESULT_REVALIDATION.md`. All from the single `ShadowBook` code path.
