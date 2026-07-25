# PRE-REGISTRATION — Trading Study 1: Volatility-Forecast Stop/Target Adaptation (MES)

> **Status: FROZEN — 2026-07-24.** First study of the trading-value program.
> The volatility model is FROZEN (Target A v2, validated); the only variable here
> is how its forecast is *used*. No element below may be changed without a new
> pre-registration. Development-only until the single hold-out confirmation; the
> hold-out (2025-01-01 → 2026-07-09) stays locked unless the dev gate passes.

## 0. Question & hypothesis
Does the frozen, validated 30-minute realized-volatility forecast improve **executable trading performance** when used to set stop/target distances, **beyond a strong naive HAR volatility estimate**, net of realistic costs?
- **H1 (primary):** replacing the naive HAR volatility with the frozen forecast, in an otherwise identical dynamic stop / fixed target overlay, raises the out-of-sample **net daily Sharpe**.
- Honest prior: the forecast beats HAR by only **+2.3% QLIKE** OOS, so a null (forecast ≈ naive) is a plausible, valid outcome. This study is designed so a null is as informative as a pass.

## 1. Relationship to prior work
- Builds on `market_state` Target A (Expected Realized Volatility), **VALIDATED and FROZEN** (`market_state/docs/RESULT_v2.md`). The model is never re-fit here.
- This is a **trading** study: the endpoint is money (risk-adjusted return), not QLIKE.
- The direction question is out of scope (previously found null); the forecast is direction-agnostic and is used only to scale *range*, never to choose *side*.

## 2. Frozen volatility sources (three, one identical causal interface)
Each source exposes a causal forward-30-min **variance** estimate `V̂_source(t)` at each completed 5-min bar, cached as a fixed timestamp→value artifact (never re-fit inside this study):
- **`forecast`** — the frozen Target A v2 model output. Dev = walk-forward OOS; hold-out = the dev-trained frozen model. (Reuse the exact artifacts already produced.)
- **`naive`** — the **HAR** baseline's forward variance (the strong benchmark). Same causal construction: dev = walk-forward OOS; hold-out = dev-trained. (Reuse `market_state` HAR baseline.)
- **`none`** — constant (`V̂ ≡ 1`); the range does not adapt to volatility.
All three are leakage-safe by the same mechanism used for Target A.

## 3. Range distance D(t) and dev-only normalization
```
D_raw_source(t) = P(t) · sqrt( V̂_source(t) )          # expected ~1-SD 30-min move, price points
D_source(t)     = c_source · D_raw_source(t)
```
- `c_source` (one scalar per source) is computed **on development eligible bars ONLY**, so that `mean(D_source)` over dev equals the **naive** arm's dev-mean raw distance (⇒ `c_naive = 1`; `forecast` and `none` rescaled to match naive's average). Normalization is global over all eligible bars (strategy-independent).
- `c_source` is **frozen and reused unchanged on the hold-out** — never recomputed there.
- Effect: all arms share the same *average* absolute range; they differ only in *time-variation*. This isolates the forecast's contribution.
- When a fresh estimate is unavailable at a bar (e.g. `forecast` after 15:25 ET), **carry forward the last available D**; identical rule for all sources.

## 4. Channel mechanics (dynamic ratcheting stop + FIXED target)
Per trade, given entry price `E0`, direction `d ∈ {+1 long, −1 short}`, entry bar `t0`:
- **Fixed take-profit (set once, never updated):** `TP = E0 + d · m · D(t0)`.
- **Dynamic ratcheting stop (updated each completed bar):** let `X(t)` = the most favorable price reached through bar `t`. Raw stop `S_raw(t) = X(t) − d · k · D(t)`. The active stop **only tightens**: `S(t) = ratchet(S(t−1), S_raw(t))` (moves in the favorable direction only, never loosens).
- Levels computed at the **close of bar t** are the fixed active levels during **bar t+1** (no intrabar look-ahead, no intrabar updates).
- **Exit** = whichever occurs first: stop, target, the strategy's own baseline exit, or session-flat 15:55 ET.

## 5. Base strategies (two generic, parameter-light classes; fixed a priori, never tuned)
Entries and baseline exits are **identical across all three vol sources** (so they cannot confound the forecast-vs-naive comparison). Both trade MES 5-min RTH bars, one position, flat 15:55 ET, entries only within **10:00–15:25 ET** (valid-forecast window).
- **Trend-following — EMA crossover:** EMA(9) vs EMA(21). Enter (next-bar open) long when EMA9 crosses above EMA21, short when below. Baseline exit = opposite cross.
- **Mean-reversion — VWAP fade:** session VWAP; enter counter-trend when `|close − VWAP| > 1.5 · ATR(14)` (long if below, short if above). Baseline exit = touch of VWAP. **ATR(14) is used ONLY to define this entry threshold and is byte-identical across all three volatility-source arms (none / naive / forecast); it never enters the stop/target range `D(t)` and therefore cannot confound the forecast-vs-HAR comparison.**

## 6. Configurations (fixed a priori)
Three (k, m) pairs — stop multiple k, target multiple m — identical across both strategies and all three vol sources ⇒ **6 forecast-arm configs** (2 strategies × 3 pairs):
- **C1** symmetric (k=1.0, m=1.0)
- **C2** let-winners-run (k=1.0, m=2.0)
- **C3** wide-stop / quick-target (k=1.5, m=1.0)

## 7. Execution realism, sizing, costs
- **Sizing is fixed** (constant 1-contract exposure) in this study — position sizing is Study 2. All Sharpe differences come purely from the stop/target-driven return distribution.
- **Fractional contracts** (continuous exposure) for the research metric; integer-lot realism is a secondary robustness report only.
- **Fills:** entry at next-bar open ± slippage; stop/target at level ± slippage; on a gap, the **stop** fills at the bar open (adverse), the **target** at the level (no favorable-gap credit); same-bar both-touched ⇒ **stop-first (adverse)**.
- **Costs (net from the first result):** the existing `mnq_system` MES cost model — commission + **1-tick spread** + **1-tick-per-side slippage** (MES point value $5, tick 0.25 = $1.25).
- **Identical capital, leverage cap, and risk constraints across all arms.**

## 8. Primary & secondary metrics
- **Primary quantity:** out-of-sample **net daily Sharpe improvement of the forecast arm vs the naive (HAR) arm** (`ΔSharpe = Sharpe_forecast − Sharpe_naive`), computed **per configuration**. Sharpe = mean(daily net PnL)/std(daily net PnL) × √252. The Go/No-Go significance is the **multiple-testing-corrected per-configuration test in §11** (corrected across all 6 configs). Config consistency and the mean ΔSharpe across configs are **reported as supporting evidence, never as a veto** — different (k,m) geometries have different payoff structures, so the forecast need not help all of them.
- **Secondary (reported, not gates):** raw net PnL, CAGR, max drawdown, Calmar, profit factor, turnover, trade count, tail losses (e.g. 1% worst days), per-strategy and per-config breakdowns, per-year and per-month ΔSharpe, config-consistency count, integer-lot variant.

## 9. Inference
- **Paired day-level block bootstrap** (5-day blocks, 5000 resamples, fixed seed): resample days jointly across arms, recompute ΔSharpe, report `P(ΔSharpe ≤ 0)`.
- **Deflated Sharpe Ratio** applied across the **6** pre-declared configs to guard the forecast arm's absolute Sharpe against multiple-testing inflation.
- Rule-based strategies (no fitting); the vol sources are already causal/OOS, so "walk-forward" here is just the chronological dev evaluation.

## 10. Locked periods
- **Development:** 2019-05 → 2024-12 (MES). All design, normalization, and the dev gate here only.
- **Hold-out:** 2025-01-01 → 2026-07-09 — **LOCKED**, evaluated once, only if the dev gate passes (enforced by the hold-out guard).

## 11. Go / No-Go
The primary test is the pre-registered **forecast-vs-HAR Sharpe improvement, corrected across all six configurations** — not a consistency requirement. Config consistency is reported, never a veto (different (k,m) geometries carry different payoff structures; a genuine effect may exist only where the exit geometry is compatible with volatility-adaptive risk management).

**Dev gate — ALL required:**
1. **Primary (multiple-testing-corrected):** at least one of the 6 pre-registered configs shows forecast-vs-HAR `ΔSharpe > 0` with paired day-level block-bootstrap evidence that survives correction across all 6 configs — i.e. that config's `P(ΔSharpe ≤ 0) ≤ 0.05 / 6 = 0.0083` (Bonferroni; the base bootstrap level is P ≤ 0.05, family-wise-corrected for 6). The **Deflated Sharpe Ratio** (across the 6 configs) is reported as a second guard on the forecast arm's absolute Sharpe.
2. **Positive net PnL:** the qualifying config's forecast arm has total net PnL > 0.
3. **Drawdown:** the qualifying config's forecast-arm max drawdown is not worse than its naive-arm max drawdown by more than **10% (relative)**.
4. **Not concentrated in one period:** the qualifying config's forecast-vs-HAR daily improvement stays positive after **dropping the single best calendar month** AND after **dropping the single best year** — the effect is not an artifact of one isolated period.

**Reported alongside (supporting evidence, NOT gates):** `Sharpe_forecast > Sharpe_none`; config-consistency count and mean ΔSharpe across the 6 configs; per-year and per-month ΔSharpe; DSR; all secondary metrics.

**Hold-out confirmation (once):** evaluate ONLY the config(s) that passed the dev gate. **GO** iff at least one dev-qualifying config shows, on the hold-out, forecast-vs-HAR `ΔSharpe > 0` **and** paired block-bootstrap `P(ΔSharpe ≤ 0) ≤ 0.05` (Bonferroni-corrected for the number of dev-qualifying configs) **and** forecast-arm net PnL > 0. Otherwise **NO-GO**.

**What a GO claims — and what it does NOT (mechanism validation, not parameter optimization):**
- **Study success** means: *at least one pre-registered configuration survives all statistical corrections and confirms on the hold-out.* Its meaning is an **existence result about the mechanism** — evidence that *dynamic, volatility-adaptive stop management can create economic value beyond HAR under at least one pre-registered implementation.*
- It is **NOT a configuration-preference or optimality claim.** The qualifying config is an existence proof only. This study makes **no claim that the winning (k,m) geometry is best, optimal, or recommended**, and **no claim of relative ranking among the six configurations** — those are explicitly out of scope. The conclusion is *"the mechanism showed economic value,"* never *"C2 is the best stop geometry."*
- Selecting or tuning a preferred stop geometry would require a **separate, future study** with its own pre-registration; the six configs here exist only to test the mechanism across a small, fixed spread of payoff structures, not to be compared for a winner.

**Hold-out confirmation (once):** mean ΔSharpe (forecast − naive) > 0 **and** paired block-bootstrap `P ≤ 0.05` **and** forecast-arm net PnL > 0 ⇒ **GO**. Otherwise **NO-GO**.

## 12. Multiple-testing controls
One channel; one instrument; one naive benchmark; 6 pre-declared configs; two fixed generic strategies; hold-out touched once. **No parameter search, adaptive pruning, post-hoc strategy/config addition, or channel cherry-picking.** DSR across the full pre-declared count. Other channels (dynamic TP, position sizing, filters, selection) are **separate future pre-registrations**.

## 13. Stopping rules & scope
- If the dev gate fails → **NO-GO for this channel**; do not iterate on dev; the hold-out is not touched (the single confirmation runs only if the dev gate passed). No parameter/strategy/config changes to rescue a failed dev result.
- **On a NULL, run a pre-specified (dev-only, non-gating) diagnostic** to distinguish *why* it failed, before any decision about further work:
  - **How different are the arms?** Report the correlation between the forecast and HAR `D(t)` streams, the fraction of trades whose exit (stop/target/baseline) differs between the forecast and naive arms, and the distribution of per-trade PnL differences.
  - **Interpretation:** if the arms are nearly identical (high `D(t)` correlation, few differing exits) → the +2.3% edge is **too small to move trades** ("too small to monetize"). If the arms differ materially but Sharpe does not improve → the null is **specific to dynamic stop/target adaptation** (the information exists but this channel doesn't capture it).
- **Position sizing (or any other channel) is NOT pre-committed.** Whether to run a Study 2 requires a **fresh decision and a separate pre-registration**, informed by the diagnostic above — it does not happen automatically on either a pass or a fail.

## 14. Interpretation of success / failure
- **Success:** the forecast produces more net return per unit of realized risk than a strong, free HAR estimate, out-of-sample, net of costs — the first evidence of genuine *economic* value, specifically in downside-risk management with a fixed objective. This validates the **mechanism** (volatility-adaptive stop management), **not** a specific stop geometry; see §11 "What a GO claims." The claim is deliberately modest: *value exists under at least one pre-registered implementation*, not *this configuration is optimal*.
- **Failure:** the forecast is statistically valid but economically redundant with cheap HAR volatility *for this use* — valuable to know, and it redirects effort (to sizing, or away from this forecast for trading).
- Neither outcome speaks to *direction*; this study never trades on the sign of the forecast.
