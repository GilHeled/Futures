# Exit-model characterization (dev, engine frozen; pre-registered, no optimization)

Trades: 273 distinct engine recommendations (MES+MNQ dev). Metrics are descriptive.

## All engine recommendations

| model | scored | win rate | expectancy R | median R | capped@5R | total R | top-5 share |
|---|---|---|---|---|---|---|---|
| fixed_2R | 165 | 0.497 | 0.491 | -1.0 | 0.491 | 81.0 | 12.0% |
| fixed_3R | 179 | 0.307 | 0.224 | -1.0 | 0.224 | 40.1 | 37.0% |
| be_after_1R | 150 | 0.113 | 0.528 | 0.0 | 0.229 | 79.2 | 84.0% |
| partial_runner | 165 | 0.497 | 0.501 | -1.0 | 0.406 | 82.6 | 49.0% |
| structural_target | 203 | 0.158 | 0.21 | -1.0 | -0.149 | 42.6 | 192.0% |

## v1 TRADE subset

| model | scored | win rate | expectancy R | median R | capped@5R | total R | top-5 share |
|---|---|---|---|---|---|---|---|
| fixed_2R | 111 | 0.477 | 0.432 | -1.0 | 0.432 | 48.0 | 21.0% |
| fixed_3R | 120 | 0.308 | 0.226 | -1.0 | 0.226 | 27.1 | 55.0% |
| be_after_1R | 98 | 0.112 | 0.567 | 0.0 | 0.193 | 55.5 | 111.0% |
| partial_runner | 111 | 0.477 | 0.462 | -1.0 | 0.351 | 51.2 | 70.0% |
| structural_target | 137 | 0.146 | 0.09 | -1.0 | -0.229 | 12.3 | 526.0% |

## v1 PASS subset

| model | scored | win rate | expectancy R | median R | capped@5R | total R | top-5 share |
|---|---|---|---|---|---|---|---|
| fixed_2R | 54 | 0.537 | 0.611 | 2.0 | 0.611 | 33.0 | 30.0% |
| fixed_3R | 59 | 0.305 | 0.22 | -1.0 | 0.22 | 13.0 | 115.0% |
| be_after_1R | 52 | 0.115 | 0.455 | 0.0 | 0.297 | 23.7 | 134.0% |
| partial_runner | 54 | 0.537 | 0.581 | 1.0 | 0.52 | 31.4 | 83.0% |
| structural_target | 66 | 0.182 | 0.458 | -1.0 | 0.016 | 30.3 | 177.0% |

*capped@5R* = expectancy with each trade capped at +5R (fat-tail robustness); *top-5 share* = % of total R from the 5 biggest winners (fragility).

---

## Interpretation — which exit matches the engine's behavior

The robustness columns (capped@5R, top-5 share) are the ones that matter here, since the whole
problem was outlier-dependence.

**fixed_2R is, clearly, the exit that matches the engine's behavior.**
- Expectancy +0.49R with ~50% win rate — and the capped@5R expectancy is IDENTICAL (+0.49R) because
  a 2R exit never produces an outlier. **Top-5 winners contribute just 12% of total R** (vs 192% for
  the structural target). The fat tail is gone; the distribution is steady and positive.
- This is exactly consistent with the earlier diagnostic (MFE median 2.46R): price reliably runs
  ~2R in the engine's favor, so harvesting at 2R captures the move the engine actually produces.

**Ranking by robustness (all recs):**
1. **fixed_2R** — +0.49R, ~50% win, top-5 12% → robust, non-fat-tailed. Best match.
2. **partial_runner** — +0.50R, capped +0.41, top-5 49% → good; banks 1R often, runner adds a
   modest (still somewhat tail-dependent) bonus.
3. **fixed_3R** — +0.22R, top-5 37% → gives back too much; many trades reach 2R but not 3R.
4. **be_after_1R** — headline +0.53R but win rate 11%, top-5 84%, median 0R → still relies on the
   distant target; it only cuts losers to 0, keeping the fat tail.
5. **structural_target** (current default) — +0.21R raw but capped@5R NEGATIVE, top-5 192% → purely
   outlier-driven. Worst.

**Cross-check:** the ordering is the same on the v1 TRADE and v1 PASS subsets (fixed_2R best on both;
PASS even slightly better at +0.61R). So the exit finding is independent of the execution filter —
the entry filter is orthogonal to exit quality, as expected.

## Conclusion (characterization, not a decision)
The engine's structural liquidity target is an ANALYTICAL objective, not a good mechanical exit. The
engine's real, harvestable behavior is a ~2R favorable move at roughly a coin-flip rate — a steady
+0.5R/trade profile with no outlier dependence when exited at 2R. Whether 2R becomes the default
execution exit is a SEPARATE decision for the user. Caveats: dev-only, costs/slippage not modelled,
not yet confirmed on the locked OOS.
