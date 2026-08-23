# Execution-factor separation — Batch-1 + Batch-2 (descriptive; no v1 fit)

- **n = 102**  (39 TRADE / 63 PASS); execute_live labelled on 60
  - active_learning_queue: 42 (38 TRADE / 4 PASS)
  - execution_selectivity_batch2: 60 (1 TRADE / 59 PASS)

## Per-factor separation

| factor | mean TRADE | mean PASS | Cohen d | AUC(take) | r(take) | r(execute) |
|---|---|---|---|---|---|---|
| pd_location | 0.3852 | 0.0408 | 1.529 | 0.913 | 0.6 | 0.703 |
| ce_distance | 0.5813 | 0.504 | 0.279 | 0.592 | 0.136 | -0.231 |
| rr_realism | 0.7988 | 0.8595 | -0.21 | 0.418 | -0.103 | -0.227 |
| confirmation | 0.7881 | 0.8197 | -0.217 | 0.469 | -0.106 | 0.046 |
| fvg_location | 0.7832 | 0.743 | 0.239 | 0.571 | 0.116 | 0.182 |

## Factor-factor correlation

| | pd_location | ce_distance | rr_realism | confirmation | fvg_location |
|---|---|---|---|---|---|
| pd_location | 1.0 | -0.35 | -0.19 | -0.14 | 0.22 |
| ce_distance | -0.35 | 1.0 | -0.19 | -0.11 | -0.04 |
| rr_realism | -0.19 | -0.19 | 1.0 | -0.04 | 0.12 |
| confirmation | -0.14 | -0.11 | -0.04 | 1.0 | -0.27 |
| fvg_location | 0.22 | -0.04 | 0.12 | -0.27 | 1.0 |

## Partial correlation with take, controlling for pd_location

- ce_distance: 0.462
- rr_realism: 0.013
- confirmation: -0.027
- fvg_location: -0.018

## Standardized multivariate logistic coefficients (unique contribution)

- pd_location: 2.311
- ce_distance: 1.605
- rr_realism: 0.319
- confirmation: 0.078
- fvg_location: -0.105

## Candidate hard rule: pd_location == 0 → PASS

- PASS with pd0: 56/63
- TRADE with pd0 (would be FALSE-blocked): 6/39
- pd0 by round: {'active_learning_queue': 6, 'execution_selectivity_batch2': 56}

## reason_for_pass frequency (PASS rows)

- premium_discount: 58
- too_far_from_ce: 24
- rr_misleading: 8
- fvg_location: 6

---

## Interpretation (answers to the 4 questions)

1. **Does `pd_location` explain the Batch-2 divergence?** YES, decisively. It is the single dominant
   separator: mean 0.385 (TRADE) vs 0.041 (PASS), Cohen d 1.53, AUC 0.913, r(execute) 0.70. 56/59
   Batch-2 PASS scenes sit at pd_location=0.
2. **Was v0's `confirmation` dominance a Batch-1 artifact?** YES. On the balanced 102-scene set
   `confirmation` does not separate at all (AUC 0.469, Cohen d −0.22, partial-corr −0.03, std-logit
   coef +0.08). Its v0 logistic coef of +1.52 was an artifact of Batch-1's 5-PASS imbalance.
3. **Is `rr_realism` useful or a proxy for bad location?** NEITHER. AUC 0.418 (slightly wrong
   direction), partial-corr with take given pd_location = 0.013 → it is essentially noise here. The
   human's `rr_misleading` reason (8×) is real but the current `rr_realism` factor (which penalizes
   raw RR>8) does not capture "distant/implausible target" — a candidate for later REDEFINITION, not
   a weight change now.
4. **Does `fvg_location` add independent info?** Minimal. Univariate AUC 0.571 but partial-corr after
   location −0.018. Keep it secondary.

`ce_distance` is the one non-obvious winner: weak alone (AUC 0.592) but the strongest INDEPENDENT
signal after location (partial-corr 0.462, std-logit coef 1.60) — it rescues wrong-side entries that
sit near CE.

## Proposed smallest v1 (deterministic, architecture unchanged)

Replace v0's confirmation-heavy logistic with a transparent weighted mean of the two factors that
actually separate:

    execution_quality = 0.6 * pd_location + 0.4 * ce_distance      (confirmation/rr_realism/fvg_location = 0)
    TRADE iff execution_quality >= 0.39

- **Higher weight:** pd_location (primary), ce_distance (secondary-primary, independent).
- **Secondary (weight 0):** confirmation, rr_realism, fvg_location — kept in the factor vector and in
  the explainability output, but not scored, since none adds separation on the combined data.
- **No hard PASS condition.** `pd_location == 0 → PASS` would false-block 6/39 TRADEs (all Batch-1,
  pd0 but high ce_distance) — location must stay a soft, heavily-weighted term.

### Validation on Batch-1 + Batch-2 together (n=102, 39 TRADE / 63 PASS)

| model | PASS recall | TRADE kept | false-PASS | over-PASS | balanced acc |
|---|---|---|---|---|---|
| v0 logistic @0.5 | 0.41 | 0.67 | 0.33 | 0.59 | 0.54 |
| **v1 (0.6·pd+0.4·ce) @0.39** | **0.89** | **0.87** | 0.13 | 0.11 | **0.88** |

Calibration (v1) is monotone: q<0.15 → 0% TRADE, [0.15,0.30) → 6%, [0.30,0.45) → 59%, [0.45,0.60) →
80%, ≥0.60 → 100%. The 5 residual false-PASSes are all Batch-1 wrong-side-but-near-CE entries — the
irreducible discretionary cases these five factors don't encode.

**Caveats:** the threshold (0.39) is grid-picked in-sample (1 dof); the weights are hand-set from the
separation table (not fitted), so this is a proposal to confirm on the next batch, not a frozen model.
