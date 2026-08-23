# Exit-model characterization (LOCKED OOS 2025-01-01..2026-07-09, engine frozen; pre-registered, no optimization)

Trades: 77 distinct engine recommendations (MES+MNQ dev). Metrics are descriptive.

## All engine recommendations

| model | scored | win rate | expectancy R | median R | capped@5R | total R | top-5 share |
|---|---|---|---|---|---|---|---|
| fixed_2R | 42 | 0.595 | 0.786 | 2.0 | 0.786 | 33.0 | 30.0% |
| fixed_3R | 43 | 0.488 | 0.953 | -1.0 | 0.953 | 41.0 | 37.0% |
| be_after_1R | 40 | 0.175 | 0.638 | 0.0 | 0.579 | 25.5 | 103.0% |
| partial_runner | 42 | 0.595 | 0.669 | 1.0 | 0.669 | 28.1 | 65.0% |
| structural_target | 49 | 0.286 | 0.896 | -1.0 | 0.484 | 43.9 | 102.0% |

## v1 TRADE subset

| model | scored | win rate | expectancy R | median R | capped@5R | total R | top-5 share |
|---|---|---|---|---|---|---|---|
| fixed_2R | 32 | 0.594 | 0.781 | 2.0 | 0.781 | 25.0 | 40.0% |
| fixed_3R | 33 | 0.515 | 1.061 | 3.0 | 1.061 | 35.0 | 43.0% |
| be_after_1R | 30 | 0.133 | 0.428 | 0.0 | 0.377 | 12.8 | 139.0% |
| partial_runner | 32 | 0.594 | 0.586 | 1.0 | 0.586 | 18.7 | 86.0% |
| structural_target | 39 | 0.282 | 0.929 | -1.0 | 0.432 | 36.2 | 122.0% |

## v1 PASS subset

| model | scored | win rate | expectancy R | median R | capped@5R | total R | top-5 share |
|---|---|---|---|---|---|---|---|
| fixed_2R | 10 | 0.6 | 0.8 | 2.0 | 0.8 | 8.0 | 125.0% |
| fixed_3R | 10 | 0.4 | 0.6 | -1.0 | 0.6 | 6.0 | 183.0% |
| be_after_1R | 10 | 0.3 | 1.268 | 0.0 | 1.187 | 12.7 | 116.0% |
| partial_runner | 10 | 0.6 | 0.934 | 1.0 | 0.934 | 9.3 | 132.0% |
| structural_target | 10 | 0.3 | 0.768 | -1.0 | 0.687 | 7.7 | 165.0% |

*capped@5R* = expectancy with each trade capped at +5R (fat-tail robustness); *top-5 share* = % of total R from the 5 biggest winners (fragility).

---

## Locked-OOS confirmation verdict — PASS ✅ (pre-registered, single touch)

fixed_2R on the LOCKED OOS (2025-01-01 → 2026-07-09, 77 trades, 42 scored):
- win rate **0.595** (≥ 0.40 ✓) · expectancy **+0.786R** (> 0 ✓)
- capped@5R **+0.786R** (identical → NO outlier dependence ✓) · median **+2.0R**
- top-5 winner share **30%** (< 50% ✓)

All four pre-registered pass conditions met. For contrast, the structural target on the same OOS is
again outlier-dependent (top-5 share 102%, capped@5R +0.48, median −1R) — fixed_2R is both more robust
AND higher on capped expectancy. **The 2R harvest generalizes out-of-sample.** The locked OOS is now
spent (touched once, as pre-registered).
