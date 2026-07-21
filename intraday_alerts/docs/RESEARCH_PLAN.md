# NEW PROJECT — ML-based intraday futures alert system (Topstep-compatible)

## Context

Fresh project, independent of the overnight strategy. Goal: discover, validate, and (only if validated) deploy a **statistically robust INTRADAY edge** for Topstep-tradable index-micro futures, surfaced as **manual-execution alerts** (never auto-trades). Reuse the prior project's *engineering discipline*, NOT its model or trading assumptions.

**Honest prior (central challenge):** the previous project found the intraday half was the *losing* half (edge was overnight-only); liquid index futures are fairly efficient intraday. So **"no intraday edge" is the base case.** Phase 1 is built to reach a *confident, cheap NO* and to treat NO as success. The failure mode to guard against is searching until something "passes." Everything is built around false-discovery control.

Phase 1 uses **only cached Databento 5-min data (MNQ/MES/MYM/M2K, 2019→2026-07-09)** — zero new data spend. Live-provider choice is deferred to Phase 2 behind a provider-independent adapter.

## Confirmed decisions (locked)

- **Instruments:** develop on **MES only**; MNQ/MYM/M2K held as **pure out-of-sample cross-market replication**.
- **Model:** **simple-first.** Stage 1 = **L2-regularized multinomial logistic** (primary). Stage 2 (HistGradientBoosting) only if Stage 1 beats nulls, is temporally stable, has a clear hypothesis, judged on identical frozen splits/costs/Go-No-Go. Banned initially: deep learning, RL, AutoML, large searches, per-instrument tuning.
- **Manual execution:** no sub-minute dependence; holding 15–60 min; ≤ 3 alerts/day.
- **Sizing:** Phase 1 uses a **fixed 1 micro** — this validates the *signal* under minimal fixed sizing only, NOT a sizing scheme. The Phase-2 position-sizing policy must be **separately frozen and validated before the production Go decision**; the alert system must **not** emit unvalidated dynamic size recommendations.
- **Topstep rules simulated inside the backtest, prospectively** (see below).
- **Provider independence** in Phase 2.

## (1) Label + model + EV — three outcomes, consistent DOLLAR units

- **Label (3-class, direction-agnostic):** from each eligible entry bar, symmetric barriers `±k·ATR`, time barrier at `min(max-hold, session-cutoff)`. Class ∈ {**UP** first, **DOWN** first, **TIMEOUT**}.
- **Model:** multinomial logistic → `P(UP)`, `P(DOWN)`, `P(TIMEOUT)`.
- **Timeout return estimated ONCE**, causally (training rows only), as the **signed price return** entry→time-barrier for a long: `tret` (price units). The short timeout outcome is exactly `−tret`.
- **EV in dollars** — every price term × `point_value`, then subtract round-trip cost (already dollars):
  - `EV_long  = point_value · [ P(UP)·(k·ATR) + P(DOWN)·(−k·ATR) + P(TIMEOUT)·tret ] − rt_cost`
  - `EV_short = point_value · [ P(DOWN)·(k·ATR) + P(UP)·(−k·ATR) + P(TIMEOUT)·(−tret) ] − rt_cost`
  - **Decision:** alert on the higher-EV side, only if that EV **> 0**. Hurdle = cost; no free threshold.

## (2) Same-bar double-touch — conservative, pre-registered

If a bar has `high ≥ up-barrier` AND `low ≤ down-barrier`, order is unknown → never infer favorably.
- **Labeling:** mark **AMBIGUOUS, exclude from training.**
- **Backtest/EV:** if a double-touch bar resolves a simulated trade, resolve **stop-first (adverse)**.
- **Report** double-touch frequency per config (a fragility signal).

## (3) Full hypothesis budget (exhaustively fixed)

- **Primary barrier config (ONE):** `k = 1.0·ATR(14)`, max-hold **30 min**. The other three (`k=1.5`; 60-min) are **pre-registered robustness only**, not selectable. Deflated-Sharpe correction applied for 4 configs.
- **Entry window (fixed):** **10:00–15:00 ET** — starts at 10:00 so the 09:30–10:00 opening-range features are fully known (no look-ahead); force-flat **15:55 ET**.
- **Exact features (fixed params, ATR-normalized where noted):** (1) OR position `(close−OR_mid)/ATR` + OR width, OR=09:30–10:00; (2) session-phase = fraction of RTH elapsed; (3) 6-bar return/ATR; (4) `(close−VWAP)/ATR`; (5) `ATR/median(ATR, 20 sessions)`; (6) overnight gap `(RTH_open−prior_close)/ATR` + return-since-open/ATR; (7) `bar_volume/median(volume same-time-of-day, 20 sessions)`. No feature added later.
- **Hyperparameters:** L2; `C` via **nested in-fold time-series CV** over fixed {0.01,0.1,1,10}; `class_weight="balanced"`; features standardized (train-fit). Not researcher-selectable.
- **Net selected axis = 1 primary config × 1 model.** Everything else fixed or auto-in-fold.

## (4) Alert-selection policy (pre-registered — enforces manual execution + max count)

Simulated **inside** the backtest (it changes which trades are realized; primary metrics are computed on this realized alert sequence, not the full label population):
- **One position at a time** — no overlapping positions.
- **Max 3 entries per day.**
- **Cooldown = 3 bars (15 min) after any exit** before a new entry.
- **Deterministic selection:** while flat and not in cooldown and < 3 entries today, at each bar take the side with higher EV if `EV>0`; ties → long before short, earliest bar first. (Phase 2 multi-instrument: highest EV across instruments, deterministic instrument-order tie-break.)

## (5) Topstep configuration — VERIFY from official source in Phase 0, then freeze

- **VERIFIED — Topstep $50K, source: help.topstep.com (Trading Combine Parameters #8284197, Maximum Loss Limit #8284204, Daily Loss Limit #10490293), retrieved 2026-07-21:**
  - **Maximum Loss Limit (trailing drawdown): $2,000** — starts $2,000 below the $50,000 balance ($48,000), trails the **end-of-day** balance upward, never moves down, and **locks permanently once it reaches the $50,000 start**. Monitored **intraday in real time** (realized + unrealized); breach ⇒ immediate liquidation.
  - **Daily Loss Limit: $1,000** (fixed at purchase for $50K). Intraday flatten when net P&L hits it; it's a **forced break for the session, not a rule violation**.
  - **Contract limit: 5 minis / 50 micros** (10:1), subject to the Scaling Plan. **Research uses 1 micro** (see sizing note).
  - **Session / cutoff:** 5 PM CT–3:10 PM CT; our strategy conservatively uses **RTH entries 10:00–15:00 ET, flat 15:55 ET** — well inside Topstep's 3:10 PM CT (≈16:10 ET) cutoff.
  - **Stage distinctions:** **Trading Combine** and **Express Funded** share these DLL parameters (DLL optional at purchase); **Live Funded** applies the DLL automatically. Research targets the **Combine → Express Funded** rule set; re-verify LFA specifics before any live-funded deployment.
- **Internal safety buffer = 20%** → effective daily stop **$800**, effective trailing MLL **$1,600**.
- **Prospective enforcement (not post-hoc):** before issuing an alert, if the trade's worst-case loss (its stop) could breach the effective daily or trailing limit, **block** it (halt entries for the day at the effective daily stop). **"Size-reduce" is NOT applicable in Phase 1** — sizing is fixed at 1 micro, so enforcement is block-only until a separate position-sizing policy is researched and validated. The simulator **reports both prevented breaches and the resulting net performance.**

## (6) Go / No-Go — numerical, using DAY-LEVEL BLOCK bootstrap

All inference via **block bootstrap over trading days** (block ≈ 5 days, 5000 resamples); strategy-vs-null via **paired block-bootstrap on the daily performance difference** (not standalone non-overlapping CIs). `R = k·ATR` risk unit; expectancy net of costs.

**Phase 1a (MES, purged walk-forward CV) — ALL required:**
- Sample: ≥ **300** realized alert-trades; ≤ **3 alerts/day** average.
- Expectancy: mean net **≥ +0.03 R**, block-bootstrap **P(mean ≤ 0) ≤ 5%**.
- Adjusted: **Deflated Sharpe p ≤ 0.05** (4 trials).
- Temporal: ≥ **4 of 6** dev years net-positive AND drop-best-year mean **> 0**.
- Nulls: **paired daily block-bootstrap** of (strategy − null) daily PnL has 90% CI **> 0** vs random-entry, always-long, always-short.
- Topstep sim: **zero** trailing-DD or daily-loss breaches under prospective enforcement.
- **Timeout-term sensitivity (numerical gate):** recompute with **timeout PnL forced to 0** (before costs). To pass, with `tret = 0`: expectancy stays **positive**, retains **≥ 50%** of the primary (with-`tret`) expectancy, AND block-bootstrap **P(mean ≤ 0) ≤ 10%**. Also report the fraction of expected performance attributable to the timeout term. Otherwise the candidate **fails** (edge too dependent on the unstable timeout estimate).
- **Fail any ⇒ STOP, report the null.**

**Phase 1b (frozen config → MNQ/MYM/M2K):** ≥ **2 of 3** with mean net > 0 and **P(mean ≤ 0) ≤ 10%**, Topstep-compliant. MES-only-pass ⇒ instrument-specific → stricter bar (default stop).

**Phase 1c (locked hold-out 2025-01→2026-07-09, ONCE):** mean net > 0, **P(mean ≤ 0) ≤ 5%**, no Topstep breach, ≤ 3 alerts/day. **GO only if all hold.**

## Data & locked hold-out (Phase 0, first)

- Dev **2019-05 → 2024-12** (MES), purged + embargoed walk-forward CV within only. **Locked hold-out 2025-01 → 2026-07-09 — untouched until 1c.** Costs net from the first result (commission + ~1-tick day-hours spread + slippage).

## (7) Exits — only what is researched

Phase 1 validates **target / stop / time-limit / session-cutoff** exits; Phase 2 alerts issue only these. The brief's "exit when conditions change" dynamic exit is **removed** — not added unless separately researched and validated as its own experiment.

## Roadmap (focused — no scope creep)

- **Phase 0:** scaffold new package `intraday_alerts/`; load cache; **lock hold-out**; commit pre-registered hypotheses + numerical Go/No-Go + frozen Topstep config; unit tests (labeling, purged-CV integrity, Topstep prospective simulator, EV 3-outcome/units). *(No modeling.)*
- **Phase 1a → 1b → 1c** with the numerical gates above.
- **Phase 2 (only on GO):** modular alert system — `provider adapter → normalized data → model → signal engine → risk filters → alert service`; only the adapter is vendor-specific; reuse the `live_validation` adapter pattern. Alerts carry instrument/side/confidence/EV/entry/stop/target/size/time-exit. No auto-execution; exits limited to the researched set.
- **Stop rule:** fail 1a/1b ⇒ documented null, project ends. No rescue experiments, no added features/models/scope.

## Reuse vs. rebuild

- **Reuse:** cached data + `CachingProvider`; block-bootstrap/stat utilities; walk-forward scaffolding (upgraded to purged+embargoed); `live_validation` adapter pattern (Phase-2 template).
- **Rebuild fresh:** features, 3-class triple-barrier labeling, multinomial model, dollar-unit 3-outcome EV, alert-selection policy, prospective Topstep-rule simulator, alert layer.

## Verification

- Unit tests: label (barrier ordering; time barrier = min(hold, cutoff); no-overnight; double-touch → ambiguous/stop-first); purged+embargoed CV (no label-horizon leakage); EV (dollar units, all 3 classes, single causal `tret`, short = −tret); alert-selection policy (one-position / ≤3-day / cooldown / deterministic); Topstep prospective simulator (blocks before breach; reports prevented breaches + performance).
- Phase-1 results vs numerical Go/No-Go; hold-out touched once.
- Phase 2 (if reached): parity test live path == batch research path.
