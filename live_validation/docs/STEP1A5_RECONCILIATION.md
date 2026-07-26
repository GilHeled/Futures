# Step 1a.5 — Reconciliation: current code path vs original validated research

**Verdict: OUTCOME 2 — the discrepancy CANNOT be fully explained. STOP.**
Do not proceed to Step 1b/1c, no spending, no live validation, until it is resolved.
— 2026-07-25

---

## What IS confirmed (identical)
- **The model is provably unchanged.** Saved bundle (`bundles/<sym>.joblib`) vs a fresh rebuild with current code, at the identical training window: **`classifier_coef`, `class_return_vec`, `feature_columns`, and `feature_config` all byte-match** (allclose) for MYM, M2K, MES. No feature/label/model drift.
- The `code_provenance=False` flag is a **false alarm**: the saved bundle recorded a `source_tree_sha256` (the working dir was not a git repo on 2026-07-20); it is a git repo now, so the *scheme* differs, not the code. The identical coefficients prove the source is unchanged.

## What is DIFFERENT (and why it was hidden)
1. **The saved bundles are LIVE bundles (train-on-all → 2026-07-09)**, not the research OOS bundle (train → 2022-12-31). Testing a 2026-07-09-trained model on 2023-2026 is in-sample; the recorded +0.63/1229 figures were a genuine 2022-12-31→OOS test. (This alone is a provenance hazard: the artifact on disk is not the artifact that was validated.)
2. **The live system is a SEQUENTIAL one-position shadow book; the validated research was a MATCHED-ENTRY (full off-hours calendar, overlaps allowed) measurement.** These are different populations. But even correcting for that, **neither reproduces the recorded numbers** (train 2022-12-31, OOS 2023-2026, @3t):

   | | research note | my matched-entry | my sequential (live book) |
   |---|---|---|---|
   | MYM | 1229 / +0.63 | 1066 / +0.745 | 817 / +0.719 |
   | M2K | 861 / +0.68 | 727 / +0.866 | 579 / +0.793 |
   | MES | 519 / +0.47 | 441 / +1.283 | 320 / +1.014 |

   Counts are **13–35% lower** than recorded, and my avg R is **systematically higher** — MES is off by ~2.7× (+1.28 vs +0.47 @3t). A stronger-than-recorded reproduction is exactly the kind of optimism that must be explained before trusting it with money.

## Why it cannot be closed right now
- **The original off-hours measurement scripts are gone.** Per the project record, the scratchpad was wiped twice; `offhours_diagnostic.py` / `offhours_temporal.py` — which produced the +0.63/1229 numbers and the temporal-robustness / null-benchmark / drop-best validation — no longer exist. A trade-by-trade reconciliation to those exact figures is therefore impossible.
- **Consequence:** the implementation that would trade live (the sequential `ShadowBook`) is **not the implementation that passed the full rigor stack** (temporal robustness, two losing null benchmarks, drop-best-window). That rigor was applied to a now-lost matched-entry measurement, and my faithful reproduction of even that measurement does not match it — and is materially stronger, MES most of all.

## Decision (pre-registered binary)
Per the Step 1a.5 criteria: **the difference is NOT fully explained → we STOP.** We cannot currently say, with confidence, that the strategy about to go live is the same strategy that produced the validated research — and the live-path reproduction is *stronger* than the record, which is a red flag, not reassurance.

## Path to resolve (before any spending or live step)
Re-establish a **single, authoritative, versioned, in-repo measurement** and put *it* through the full stack:
1. Freeze the sequential `ShadowBook` (the actual live implementation) as THE strategy of record.
2. Re-run the complete rigor stack **on that exact implementation** — gap-aware fills, the two null benchmarks (overnight-hold + random ensemble), per-year + drop-best-window temporal robustness — reproducing or superseding the +0.63/1229 research numbers *with code that still exists*.
3. Investigate the residual gap (the ~2.7× MES avg-R difference especially): rule out any look-ahead/optimism in `inference.py`/`shadow_book.py` beyond the three bugs the parity test already caught (the parity test only checks streaming≡batch — both use the same harness, so a shared optimism would pass it).
4. Only when **validated == live** (same code, reconciled numbers) do we revisit Step 1b vs direct-to-live.

**Until then, the overnight-edge live validation is on hold. No quote-data purchase, no IBKR, no live run.**
