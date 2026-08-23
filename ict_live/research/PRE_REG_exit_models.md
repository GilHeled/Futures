# Pre-registration — exit-model characterization study (FROZEN before running)

**Purpose:** characterize the natural exit behavior of the (frozen) engine's setups — NOT optimize
PnL, NOT search parameters. Compare a FIXED, small set of exit philosophies on the same trades to
understand which exit best matches how price actually behaves after the engine's entries.

**Engine frozen.** Entries, stops, and structural targets come unchanged from the engine. Only the
EXIT rule varies. No parameter is tuned; each model below has a single pre-declared configuration.

## Trade set
All distinct engine recommendations over DEV (2019-05-01 → 2025-01-01; LOCKED OOS ≥2025 untouched),
one per (contract, setup id), MES + MNQ, 1H signal / 15m entry-refine — the same replay as the
baseline. Reported overall and split by execution decision (v1 TRADE vs PASS). Initial risk = 1R =
|entry − stop|. Horizon = engine default. Fill = first bar at/after the decision containing the entry.

## The five exit models (fixed configs)
1. **fixed_2R** — full exit at +2R; else stop −1R; else horizon mark-to-close.
2. **fixed_3R** — full exit at +3R; else stop −1R; else horizon.
3. **be_after_1R** — target = structural liquidity target; initial stop −1R; when +1R is reached the
   stop moves to breakeven (0R); outcomes: TARGET (+reward_R) / BE (0R) / STOP (−1R) / horizon.
4. **partial_runner** — take 50% off at +2R and move the remaining 50% stop to breakeven; the runner
   targets the structural liquidity target. Realized R = 0.5·(+2) + 0.5·(runner: +reward_R if target,
   0 if BE, else horizon mark). If stopped before +2R: −1R (no partial).
5. **structural_target** — the current default/baseline: full exit at the structural liquidity
   target; else stop −1R; else horizon.

## Intrabar ambiguity (no look-ahead, conservative)
If within a bar BOTH the decisive favorable level (target / +NR / runner target) AND the governing
stop (initial or BE) would trigger, the trade is marked **AMBIGUOUS** for that model and EXCLUDED
from the R statistics (counted separately). This matches the baseline's `AMBIGUOUS_INTRABAR` rule and
prevents optimistic bias. No-fill trades are excluded and counted separately.

## Metrics per model (descriptive; no winner-optimization)
n, filled, ambiguous, win rate, expectancy R, median R, total R, expectancy with each trade capped at
+5R (fat-tail robustness), top-5-winner share of total R (fragility), and the R distribution
(quantiles + buckets). The comparison answers which exit philosophy gives the steadiest,
least-outlier-dependent distribution — i.e. which one matches the engine's real behavior.

## Decision rule
This study DESCRIBES the exit distributions. Choosing a default execution exit is a SEPARATE decision
taken by the user after seeing these results. No model is auto-selected; no further tuning is done.
