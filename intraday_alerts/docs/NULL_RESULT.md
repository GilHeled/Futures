# NULL RESULT — Intraday edge research, Phase 1a (MES)

**Status: CLOSED — registered null hypothesis confirmed. Archived 2026-07-21.**
This research line is complete. Any future intraday work is a NEW project with a
new pre-registration, not a continuation of this experiment.

## 1. Original hypothesis
Does a **statistically robust, net-of-cost intraday directional edge** exist for
Topstep-tradable index micros (developed on MES), tradable as manual alerts under
Topstep constraints? Pre-registered base case (from the prior overnight project +
market-efficiency priors): **most likely NO.**

## 2. Frozen research design (see PRE_REGISTRATION.md / config.py)
- **MES dev only** (2019-05→2024-12); hold-out 2025-01→2026-07 **locked and never accessed**; MNQ/MYM/M2K never inspected.
- **Labels:** triple-barrier, 3-class (UP/DOWN/TIMEOUT), ATR(14)-scaled; primary k=1.0/30-min; double-touch → ambiguous(train)/stop-first(backtest); no overnight.
- **Features (9, fixed):** or_position, or_width, session_phase, momentum_6, vwap_dev, vol_regime, overnight_gap, return_since_open, participation.
- **Model:** L2 multinomial logistic, C via nested in-fold CV; purged+embargoed walk-forward (6 annual folds, embargo=hold).
- **Decision:** 3-outcome dollar EV (single causal timeout return); alert higher-EV side only if EV>0; hurdle = round-trip cost ($5.25 MES).
- **Execution:** alert policy (one position, ≤3/day, 15-min cooldown) + prospective, block-only Topstep $50k risk simulator (MLL $2,000 / DLL $1,000, verified 2026-07-21; 20% buffer).
- **Inference:** day-level block bootstrap; strategy-vs-null = paired block-bootstrap; Deflated Sharpe over 4 barrier trials.

## 3. Go/No-Go criteria (Phase 1a — all required)
≥300 trades; ≤3 alerts/day; mean net ≥ +0.03R with block-bootstrap P(mean≤0) ≤ 5%; Deflated-Sharpe p ≤ 0.05; ≥4/6 dev years positive + drop-best-year > 0; beats random/always-long/always-short (paired CI > 0); zero Topstep breaches; timeout gate (tret=0: positive, ≥50% retained, P≤10%).

## 4. Final numerical results — NO-GO (fails at the first criterion)
- **Primary config fired 2 trades in 5.5 years** (need ≥300). Over ~26.7k scored bars, **0.0072% had EV > 0** after cost (best EV −$2.71, median −$4.78).
- Every downstream criterion is therefore unmet or vacuous. Decisive fail.

## 5. Key diagnostics (why it failed)
- **No directional skill.** Mean OOS class probabilities ≈ uniform (UP 0.334 / DOWN 0.333 / TIMEOUT 0.332). Confusion matrix: true-UP and true-DOWN rows receive near-identical predictions; accuracy 0.314 < 0.438 base rate. Calibration of P(UP) is flat (predicted 0.25→0.41 → empirical ~0.42–0.45).
- **Directional coefficients are negligible.** Standardized UP-minus-DOWN log-odds max ≈ 0.028/SD (overnight_gap). The only sizeable coefficients load on the TIMEOUT axis (momentum_6 +0.124, session_phase −0.109): the features predict *whether* a barrier is hit (volatility), not *which* direction.
- **Directional-gap distribution never approaches the hurdle.** P(UP)−P(DOWN): std 0.0154, p1 −0.038 / p99 +0.040, max 0.101. EV>0 requires the gap > ~0.22–0.42 (`P_up−P_down > (rt_cost − pv·P_to·tret)/(pv·k·ATR)`). Observed max is <½ of the smallest hurdle.
- **Underfit, not overfit.** Per fold, train log-loss ≈ test log-loss ≈ uniform (1.086 vs 1.0986) — nothing learnable, not overfitting.
- **Does not beat trivial rules.** Frictionless directional accuracy: ML lean 0.5015 (+0.0019R) — marginally the *worst* non-short method; momentum>0 0.5059, return_since_open>0 0.5053, always-long 0.5027 — all ≈ chance / ≈ 0R.
- **Costs are not the cause.** Frictionless (zero commission/spread/slippage) mean realized R = +0.008, win rate 50.5% → **Case 1: no predictive signal even in a frictionless market**, not "signal too small for costs."

## 6. Final conclusion
The frozen 9-feature multinomial-logistic model contains **essentially no economically useful intraday directional information for MES**. The registered null is confirmed. The pre-registration, locked hold-out, fixed hypothesis budget, and stop rule delivered a clean null with no p-hacking, no hold-out contamination, and no rescue searching.

## 7. Lessons learned
- **Pre-registration + a hard stop gate worked as designed** — the experiment ended at the failure gate instead of drifting into a search that would have manufactured a false positive.
- A **frictionless counterfactual** is the cleanest way to separate "no signal" from "signal killed by costs" — worth keeping as a standard diagnostic.
- **Trivial-heuristic baselines are essential**: an ML model that cannot beat "momentum > 0" has no business being trusted.
- ATR-scaled *intraday* barriers are small relative to realistic costs (cost was ~25–52% of the barrier), so the economic bar is high — but here the binding constraint was signal, not cost.

## 8. Recommendations for future research
- Treat any future intraday effort as a **completely new project with a new pre-registration.** Do not extend or re-tune this one.
- **Do not reuse this feature family unchanged for directional prediction.** These features characterize **volatility / barrier-timing**, not direction — a genuinely different, more orthogonal feature set (or a different target) would be required to have any chance.
- The demonstrated **volatility/timeout predictability could be repurposed** for a *different objective* (e.g., volatility-regime or activity forecasting, position-sizing, or session-risk filtering) — but that is a separate hypothesis, not a directional edge.
- If pursued, consider different targets (longer intraday horizons, event/regime-conditioned setups) and, only if justified, richer data (order-flow/microstructure) — while carrying the honest prior that liquid index futures are efficient intraday.
