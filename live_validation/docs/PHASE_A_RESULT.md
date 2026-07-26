# Phase A Result — Causality Audit: FAIL (material look-ahead leak)

**Verdict: the implementation is NOT causally correct. A look-ahead leak accounts
for essentially the entire overnight edge. — 2026-07-26**

## The leak
`mnq_system/modeling/features.py::build_feature_matrix` computes **`gap_atr_ratio`** (and, by the same structure, **`overnight_imbalance_pctile`**) **per calendar date** and assigns the value to *every* bar of that date. But the gap = *that day's 09:30 ET open* − prior-session close, and the overnight imbalance spans until 09:30 ET. So for any bar **before 09:30 ET** (the whole overnight / pre-open window where this strategy trades), these features use **future** same-day information.

`gap_atr_ratio` for an overnight bar is, definitionally, ~the overnight move the strategy is trying to predict. The model wasn't forecasting the overnight direction on pre-open bars — it was **reading it**.

## Proof (prefix-stability test)
For a 04:40 ET pre-open bar, the feature matrix computed on the full series vs on the series **truncated at that bar** (no future):

| feature | full (uses future?) | truncated (causal) | |
|---|---|---|---|
| gap_atr_ratio | −4.47356 | **NaN** | *** LEAK *** |
| trend_slope_pct / vwap_distance_atr / bars_since_sweep | (match) | (match) | ok |

The gap value only exists once the day's 09:30 open has printed; a causal computation has NaN. Definitive look-ahead.

## Footprint — the edge IS the leak
Frozen model (train 2022-12-31), sequential ShadowBook, OOS 2023-2026, net @3t, split by whether the entry bar is in the leaked (pre-09:30) window:

| | LEAKED (pre-09:30) | CAUSAL (≥09:30) |
|---|---|---|
| MYM | n=544, avg R **+1.22**, +661R | n=273, avg R **−0.27**, −73R |
| M2K | n=388, avg R **+1.20**, +467R | n=191, avg R **−0.04**, −8R |
| MES | n=191, avg R **+1.59**, +305R | n=129, avg R **+0.16**, +20R |

~67% of trades sit in the leaked window and carry **all** the positive R; the causally-clean entries are breakeven-to-negative. The edge does not survive removal of the look-ahead window.

## Implications (grave)
- **The current implementation's edge is a look-ahead artifact**, not skill.
- **The prior "validated" overnight research is contaminated by the same bug** — Rounds 13–14 used this same `features.py`. The null benchmarks (overnight-hold, random) do not use the model, so they never had the leak; "the model beats the benchmarks" was the leak talking. The full rigor stack (temporal robustness, drop-best, benchmarks) was applied on top of leaked features and therefore does **not** establish a real edge.
- This is exactly why we stopped before spending a dollar. The chain of evidence is broken at its root — the signal itself.

## Mandated next step (per the frozen §2 gate)
1. **Fix the leak:** make `gap_atr_ratio` and `overnight_imbalance_pctile` strictly causal — a bar before 09:30 ET must not see that day's open/overnight-window; the gap/imbalance are simply undefined (NaN) until the window completes (or must reference the *prior completed* session only).
2. **Retrain the frozen model on corrected features** and re-measure on the sequential ShadowBook.
3. **Honest prior:** given the causal-window entries are already breakeven-to-negative, the overnight edge is **likely not real**. If the corrected, causal implementation shows no robust net-of-cost edge, the overnight project **closes** as a look-ahead null — the same disciplined outcome as the volatility-monetization line.

Only if a genuine edge survives on strictly causal features do we write and freeze the statistical protocol (§3–§9) and continue. **No spending, no live, no trading — and no statistical validation of the leaked implementation.**
