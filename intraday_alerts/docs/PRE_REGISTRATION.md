# Pre-registration — intraday edge research (FROZEN 2026-07-21)

Machine-readable source of truth: `intraday_alerts/config.py`. This document is
the committed, human-readable freeze. **Changing any item here or in config.py
terminates this experiment and requires registering a new one.**

## Question
Does a statistically robust, net-of-cost **intraday** edge exist for Topstep-tradable
index-micro futures? Base-case expectation: **NO** (the prior project found the intraday
half was the losing half). "No" is a valid, likely, and acceptable outcome. Design fails
cheap; no rescue searching.

## Frozen design
- **Develop on MES only.** MNQ/MYM/M2K are pure out-of-sample cross-market replication.
- **Locked hold-out 2025-01-01 → 2026-07-09** — untouched until Phase 1c (enforced in code by `data.load_bars`).
- **Labeling:** triple-barrier, 3-class (UP/DOWN/TIMEOUT), ATR(14)-scaled. Same-bar double-touch → AMBIGUOUS (excluded from training) / stop-first (adverse) in backtest. Time barrier = min(hold, session cutoff) ⇒ no overnight.
- **Primary barrier:** k=1.0·ATR, 30-min hold. Robustness-only (not selectable): {k=1.0/60m, k=1.5/30m, k=1.5/60m}. Deflation over 4 trials.
- **Features (fixed, 9):** or_position, or_width, session_phase, momentum_6, vwap_dev, vol_regime, overnight_gap, return_since_open, participation.
- **Model:** L2 multinomial logistic; `C` via nested in-fold time-series CV over {0.01,0.1,1,10}; class_weight balanced; standardized (train-fit). No hand-selection.
- **Entry window 10:00–15:00 ET; force-flat 15:55 ET.**
- **EV (dollars):** 3-outcome, single causal timeout return `tret` (short = −tret); alert on higher-EV side only if EV>0; hurdle = cost.
- **Alert policy:** one position; ≤3 entries/day; 15-min cooldown; deterministic (higher EV; ties long-before-short, earliest bar). Simulated in-backtest; metrics on the realized alert sequence.
- **Costs:** commission $1.50/RT + 1-tick spread + 1-tick/side slippage.
- **Topstep $50k (verified — see TOPSTEP_RULES_2026-07-21.md):** MLL $2,000 trailing/locks-at-start; DLL $1,000; 1-micro research size; 20% buffer ⇒ eff daily $800 / eff MLL $1,600; enforced **prospectively, block-only**.
- **Inference:** day-level **block bootstrap** (5-day blocks, 5000 resamples); strategy-vs-null via **paired** block-bootstrap of the daily difference.

## Numerical Go / No-Go
**Phase 1a (MES, purged WF-CV) — all required:** ≥300 trades; ≤3 alerts/day; mean net ≥ +0.03R with P(mean≤0) ≤ 5%; Deflated Sharpe p ≤ 0.05; ≥4/6 dev years positive + drop-best-year > 0; beats random/always-long/always-short (paired daily block-bootstrap CI > 0); zero Topstep breaches; **timeout gate:** with tret=0 (pre-cost) expectancy stays positive, retains ≥50% of primary expectancy, P(mean≤0) ≤ 10%.
**Phase 1b:** frozen config replicates on ≥2/3 of MNQ/MYM/M2K (mean net > 0, P(mean≤0) ≤ 10%).
**Phase 1c:** locked hold-out, once — mean net > 0, P(mean≤0) ≤ 5%, no Topstep breach, ≤3 alerts/day.
**Stop rule:** fail 1a or 1b ⇒ documented null, project ends.
