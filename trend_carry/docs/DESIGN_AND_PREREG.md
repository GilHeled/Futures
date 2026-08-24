# DESIGN & PRE-REGISTRATION (DRAFT) — Diversified Trend + Carry (Futures)

> **Status: DRAFT for review → freeze.** This document is **implementation-independent**:
> it specifies the economic rationale, the research design, the validation methodology,
> the expected failure modes, and the frozen Go/No-Go — *not* code. No code is written
> and no new data is pulled until this design is reviewed, the open decisions in §12 are
> settled, and the document is frozen. On freeze it is persisted verbatim as
> `trend_carry/docs/PRE_REGISTRATION.md` and only then does implementation begin.

---

## 0. Objective

Determine whether a **diversified, rules-based trend + carry program on liquid futures**
produces a **persistent, robust, net-of-cost return** that we would be willing to deploy
with real capital via an IBKR futures account (hold-capable; multi-day holds — *not* the
intraday Topstep account).

This is a **risk-premium harvest**, not a prediction model. We are not forecasting
direction; we are collecting compensation for bearing risks others offload. That
distinction is the reason this is worth engineering time after three prediction-alpha
nulls: the edge, if real, is *structural* and should appear across many independent
markets and decades — the exact property that makes it validatable to destruction.

**This study does not tune a strategy to win.** It tests whether *canonical, textbook*
definitions of trend and carry survive our full rigor stack on our universe, our costs,
and our locked hold-out. If they don't, the line closes — same discipline as the
volatility-monetization and overnight lines.

## 1. Economic rationale (why these premia persist)

**Time-series momentum (trend).** Buy what has risen, sell what has fallen, size by risk.
Persistence rests on two mechanisms: (a) **behavioral under-reaction** — anchoring, the
disposition effect, and gradual information diffusion cause prices to trend rather than
jump to fair value; (b) **risk transfer** — hedgers pay speculators to absorb directional
risk, and trend-followers are the speculators providing that liquidity. Documented
out-of-sample over a *century* and across every asset class (Hurst–Ooi–Pedersen, "A
Century of Evidence on Trend-Following"; Moskowitz–Ooi–Pedersen, "Time Series Momentum").
Its payoff is **positively skewed** — it cuts losers fast and lets winners run — which
historically produces "crisis alpha" (a long-volatility-like profile in sustained
sell-offs).

**Carry.** Hold the contract whose term structure pays you to hold it. Carry = the **roll
yield** implied by the shape of the futures curve: a backwardated market (front richer
than deferred) pays a positive roll as the position ages toward the deferred price; a
contango market costs you. Persistence rests on **compensation for absorbing hedging
imbalance** (e.g., commodity producers short-hedge, pushing curves into backwardation and
paying speculators who take the long side) and for bearing storage/convenience-yield and
funding risks. Documented across asset classes (Koijen–Moskowitz–Pedersen–Vrugt,
"Carry"). Its payoff is **negatively skewed** — carry tends to "crash" in risk-off
episodes.

**Why both, together.** Trend (positive skew) and carry (negative skew) are historically
**weakly correlated and complementary**; combining them has produced materially higher
risk-adjusted return than either alone, for almost no extra infrastructure. The combined
book — not either sleeve — is the candidate product. Whether that diversification benefit
actually materializes on *our* data is itself a pre-registered test (§6, §8).

## 2. Universe (14 roots, 6 sectors — frozen values in §12)

Breadth is the mechanism that makes the premia robust and lets us validate to destruction
— but the operative quantity is **effective independent bets**, not root count. Root count
badly overstates breadth: ES/NQ/YM/RTY are ~0.9 correlated (one equity bet), the treasury
futures share one rates factor, the FX majors share one dollar factor, refined products
track crude ~0.9, and the soy/grain complex is internally correlated. Adding those
redundant roots grows the system without adding independent information.

The universe is therefore chosen for **top-tier liquidity and low within-sector
redundancy** — 14 roots that span all 6 major macro sectors with roughly the same
effective breadth (~7–9 independent bets) as a 26-root set, while being fully deployable:

| Sector | Roots (research) | Micro proxy (deploy) | Why these |
|---|---|---|---|
| Equities | ES, NQ | MES, MNQ (cached) | deepest two; YM/RTY ~0.9 redundant |
| Rates | ZT, ZN, ZB | — | 2y/10y/30y spans the curve; ZF sits between |
| FX | 6E, 6J, 6A | M6E, M6A | EUR + JPY (haven) + AUD (commodity); rest share the dollar factor |
| Metals | GC, HG | MGC, MHG | precious/haven vs industrial; SI ~0.8 w/ GC |
| Energy | CL, NG | MCL | crude + independent nat-gas; products ~0.9 w/ CL |
| Grains | ZC, ZS | — | corn + soybeans; ZW/ZL correlated additions |

- **Frozen at the outset, including markets that may turn out to be losers** — no
  survivorship/selection. The universe is declared once; we do not add or drop roots to
  improve results. (Widening toward the fuller ~26-root set is a *post-validation* scaling
  step for a modest Sharpe bump, never a mid-study change.)
- **Research on the standard-liquidity contract; deployment maps to the micro** where one
  exists. Micros track their full-size parent near-perfectly and share the identical term
  structure, so signals are unaffected; only position sizing differs.
- **Drop-best-sector / drop-best-year power comes from sector count (6), not root count**
  — 14 markets across 6 sectors is sufficient for those tests to bite.

## 3. Data & continuous-contract methodology (method, not code)

- **Source / schema:** GLBX.MDP3 (CME Globex), **daily** bars (`ohlcv-1d`). Trend and
  carry are daily-rebalanced multi-day-hold strategies; daily data is sufficient and
  cheap (see cost note, §11). Coverage confirmed 2010-06-06 → present for every root.
- **Roll rule (pre-declared, single choice):** one roll convention, fixed in advance
  (candidate: volume/open-interest roll, or a fixed N-days-before-expiry calendar roll).
  The chosen rule is frozen in §12; no post-hoc roll-rule selection.
- **Return/PnL series (trend):** a **roll-adjusted continuous series** (ratio- or
  difference-adjustment applied *backward* at each historical roll) so that returns are
  continuous and no artificial gap at a roll is mistaken for a real move. The adjustment
  method is frozen in §12.
- **Carry measurement:** computed from the **raw (unadjusted) prices of two adjacent
  ranks** (front `c.0` and next `c.1`) — carry is a spread and must use real prices, never
  the back-adjusted series. Exact definition (annualized log or simple roll yield,
  normalized by time-to-roll) frozen in §12.
- **Causality is built in from line one (the overnight-line lesson):**
  - Every signal computed on date *t* uses information available **only through *t*'s
    settlement**; the resulting position is applied to ***t+1*'s** return. No same-bar or
    future information enters any signal, roll date, vol estimate, or adjustment.
  - The roll schedule and back-adjustment must be **causal**: at date *t* they may use
    only rolls that have already occurred; no future roll price leaks into the historical
    series used for a *t*-dated decision. A **prefix-stability audit** (recompute every
    signal on a series truncated at *t* and confirm it equals the same signal from the
    full series) is a **mandatory pre-run test**, not an afterthought.
  - Carry must not read a stale/illiquid deferred quote as if tradeable; deferred-rank
    liquidity is checked before a root's carry signal is trusted.

## 4. Signal definitions (canonical, anti-overfit)

To prevent the study from degenerating into a parameter search, both signals use **small,
fixed, textbook specifications** — declared now, never tuned on results.

- **Trend (time-series momentum):** position sign = sign of a **fixed equal-weight
  ensemble of canonical lookbacks** (candidate set: {21, 63, 126, 252} trading days) on
  the roll-adjusted return of each root. No lookback is selected or weighted by
  performance; the ensemble is frozen. Per-root position is then risk-scaled (§5).
- **Carry:** position from the sign (time-series) and/or cross-sectional rank of each
  root's roll yield. One canonical form is frozen in §12 (candidate: time-series sign of
  carry, risk-scaled — long positive-carry, short negative-carry — as the primary, with
  cross-sectional rank reported as a secondary robustness view).
- **No thresholds, filters, regime switches, or conditioning variables** are added to
  "improve" either signal on the development data. Any such addition would reopen the
  overfitting door and is out of scope for this study.

## 5. Portfolio construction (frozen mechanism)

- **Per-instrument risk scaling:** each root's position is scaled by the inverse of its
  recent realized volatility (candidate: 60-day, causal) so every market contributes
  comparable risk. This is standard risk-parity-style sizing, not a tuned overlay.
- **Portfolio volatility target:** the combined book is scaled to a fixed annualized vol
  target (candidate: 10%). This is a *scaling* choice (affects magnitude, not the
  existence of an edge) and is frozen in §12.
- **Sleeves:** trend and carry are each constructed as above, then combined at a fixed
  risk split (candidate: 50/50). We report trend-only, carry-only, and combined.
- **Risk overlay:** per-instrument and per-sector position caps; a portfolio vol cap.
  Any drawdown-control rule, if used, is pre-declared in §12 (default: none, to avoid a
  tunable knob).

## 6. Validation methodology

**Data splits (frozen):**
- **Development (in-sample):** 2010-06-06 → 2019-12-31. Used only for the (minimal)
  unavoidable choices, all of which are frozen in §12 *before* seeing results.
- **Out-of-sample (OOS):** 2020-01-01 → 2024-12-31. All Go/No-Go evaluation happens here.
- **Locked hold-out:** 2025-01-01 → 2026-07-09. **Untouched** until a single final
  confirmation, and only if OOS passes. (Note: this is a *different* dataset from the
  Target-A volatility hold-out; that one stays locked and is unrelated.)

Because the design is parameter-light by construction, "development" is mostly a sanity
and plumbing stage; the scientific test is OOS + hold-out.

**Breadth robustness (the core robustness lever):** the premium must be **distributed, not
concentrated**. Pre-registered:
- Positive in a **majority of sectors**, AND **drop-best-sector** still positive.
- Positive in a **majority of OOS years**, AND **drop-best-year** still positive.
- Reported per-root so a single dominant market is visible.

**Significance:** **block bootstrap** on daily portfolio returns (block length chosen to
respect autocorrelation; fixed seed), reporting `P(mean net return ≤ 0)`. **Deflated
Sharpe Ratio** (Bailey–López de Prado) computed against the number of canonical
specifications considered, so the small multiple-comparison surface is charged honestly.

**Benchmarks the book must beat (net of cost):**
- Zero (is it positive at all).
- **Passive long equity beta** (buy-and-hold the equity roots, matched vol) — guards
  against "this is just long stocks."
- **Random-sign null** (many seeds, matched turnover and vol) — guards against "any
  diversified vol-targeted book looks fine."
- **Combined vs each sleeve** — is the trend+carry diversification claim (§1) actually
  realized on our data, or is one sleeve carrying a dead one.

**Cost model:** commission + spread + slippage per contract from realistic futures values;
turnover × cost as a daily drag (low, since holds are multi-day). **Cost sensitivity** at
1×, 2×, 3× modeled costs is part of the Go/No-Go, not a footnote.

## 7. Expected failure modes (pre-stated, each mapped to a test)

1. **Concentration** — the "edge" lives in 1–2 markets or one period → *breadth /
   drop-best-sector / drop-best-year* (§6).
2. **It's just beta** — the book is long equities in disguise → *passive-beta benchmark +
   correlation-to-SPX + is alpha positive net of beta* (§6).
3. **Costs kill it at our scale** → *cost-sensitivity 1×/2×/3×* (§6).
4. **Data artifacts** — a bad roll adjustment manufactures returns at rolls, or carry is
   read off a stale deferred quote → *prefix-stability causality audit, per-roll return
   inspection, deferred-liquidity check* (§3).
5. **Decay** — trend in particular went through a weak 2011–2019 stretch; the edge may be
   a pre-2010 relic → *per-year consistency across OOS is required*, and a book that only
   works in the distant past is a NO-GO.
6. **Overfitting via spec selection** — we quietly picked the lookbacks/carry form that
   worked → controlled by *pre-registering canonical specs (§4) + DSR (§6)*; no post-hoc
   spec changes.
7. **Capacity / fill realism** — signals fine on paper, unfillable in the thin roots →
   partially addressable offline (liquidity screens); the residual is resolved in a
   pre-registered **paper/live shadow** stage *after* a GO, before real size.

## 8. Go / No-Go (frozen thresholds — proposed; confirm in §12)

**OOS PASS requires ALL of the following, net of modeled cost, on the combined book:**
- Net Sharpe ≥ **0.5** (annualized) over the OOS window.
- Block-bootstrap `P(mean net ≤ 0) ≤ 0.05`, **and** Deflated Sharpe Ratio > 0.
- Beats the **passive-beta** benchmark and the **random-sign** null (paired, positive).
- **Breadth:** positive in a majority of sectors + drop-best-sector positive; positive in
  a majority of OOS years + drop-best-year positive.
- **Not pure beta:** correlation to SPX modest and alpha-to-equity-beta positive.
- **Diversification realized:** combined Sharpe ≥ the better single sleeve (trend-only,
  carry-only) — or, at minimum, not worse than the better sleeve.
- **Cost-robust:** still net-positive and bootstrap-significant at **2×** modeled cost.

**Hold-out confirmation (once):** on a GO from OOS, evaluate the *frozen* book once on the
locked hold-out; it must remain net-positive and block-bootstrap-significant. No parameter
touched between OOS and hold-out.

## 9. Phasing and supersede-or-close (stopping rules)

**Trend and carry are two distinct pre-registered existence tests of two independent
premia — not two tries at one hypothesis.** They are evaluated sequentially, cheapest and
least-bug-prone first, on the shared substrate:

- **Phase 1 — Trend only.** Build the shared substrate (data, volume-roll continuous,
  ratio adjustment, sizing, vol-targeting, costs, PnL, bootstrap, DSR, breadth tests,
  benchmarks, causality audit) and the trend signal, which operates on the single adjusted
  series. Run the full Go/No-Go (§8). This is the shortest path to a first decision *and*
  validates the entire substrate on the simplest signal before any two-rank complexity.
- **Phase 2 — Carry.** Added only after Phase 1's substrate is proven correct. Carry's
  marginal engineering is not zero — it needs the second rank aligned across differing
  roll timing, the time-to-expiry Δt from the definition schema, and deferred-liquidity
  checks (the bug-prone surface). Phase 2 runs carry's own Go/No-Go, then evaluates
  carry-alone and the **combined trend+carry** book (the diversification claim, §1).

**Stopping rules:**
- **Phase 1 trend GO** (OOS passes AND hold-out confirms): trend becomes a strategy of
  record; we may begin the pre-registered paper/live shadow on IBKR *while Phase 2 is
  built*. Phase 2 then tests whether carry adds value on top.
- **Phase 1 trend NO-GO does NOT close the line** — it triggers Phase 2, because carry is
  an independent premium that can pass where trend fails.
- **The line closes only if BOTH premia fail their own frozen Go/No-Go** — documented
  null, like the prior two lines.
- **Multiple-testing discipline:** each premium is judged on its *own* frozen bar with DSR
  charged within its own spec count; we never pool the two, never cherry-pick, and never
  tune one to rescue the other. No rescue tuning, no new lookbacks, no universe changes,
  no regime filters. Each phase is a single frozen protocol; a fail is not re-run as a v2
  on the same development data. Any genuinely new idea starts from a new economic premise
  and its own pre-registration.

## 10. What this study explicitly does NOT do

- Does not tune parameters to maximize backtest Sharpe.
- Does not use the intraday Topstep account (holds are multi-day → IBKR).
- Does not claim a directional forecast; positions are premium-harvesting, not predictions.
- Does not touch the Target-A volatility hold-out (unrelated, stays locked).
- Does not spend on data beyond the reviewed-and-approved pull (§11).

## 11. Data coverage & cost (verified via free metadata; no data pulled)

- **Coverage:** every proposed root trades on **GLBX.MDP3**, daily history **2010-06-06 →
  present**. No coverage gap; no second vendor needed.
- **Already owned (paid, $0 to reuse):** MES / MNQ / MYM / M2K 5-minute 2019→2026 (daily
  derivable locally at no cost).
- **Estimated cost of the new history** (via `metadata.get_cost`, a free call that
  downloads nothing): **daily bars, 42 continuous symbols (21 new roots × front + next
  rank), 2010→2026 = ≈ $1.91 total.** Well within the standing free credit; negligible.
- **Lower-cost alternative considered:** none needed — the daily pull is already ~$2. (For
  contrast, an intraday-minute pull would be ~100× larger and is unnecessary for
  daily-rebalanced trend/carry.)
- **Cost discipline:** no data is pulled until this design and this cost are approved; when
  approved, the pull is done **once**, cached in-project (`cache/bars/`), and a
  fake/exploding-provider check confirms the cache prevents any re-fetch before any repeat
  run — per the standing rule.

## 12. Finalized design (frozen values)

All degrees of freedom are resolved to a single choice; nothing is left open. These
values are frozen on approval and are not revisited after seeing results.

1. **Universe (14 roots, 6 sectors):** Equities ES, NQ · Rates ZT, ZN, ZB · FX 6E, 6J,
   6A · Metals GC, HG · Energy CL, NG · Grains ZC, ZS. Chosen for top-tier liquidity and
   low within-sector redundancy — ~7–9 effective independent bets, the same effective
   breadth as a 26-root set, but fully deployable. Research on standard-liquidity
   contract; deploy the liquid micro subset. Widening toward ~26 roots is a
   post-validation scaling step, never a mid-study change.
1b. **Phasing:** Phase 1 = trend only (shortest path to first Go/No-Go; validates the
    shared substrate on the simple single-series signal). Phase 2 = carry on the proven
    substrate, then the combined book. Trend and carry are independent pre-registered
    existence tests; a Phase-1 NO-GO triggers Phase 2 rather than closing the line; the
    line closes only if both premia fail their own frozen Go/No-Go (see §9).
2. **Roll rule:** volume roll (`.v.0` front, `.v.1` next).
3. **Back-adjustment:** ratio (proportional), applied backward at each roll, for the
   trend return series only. Carry uses the raw two-rank prices.
4. **Carry definition:** annualized log roll yield `C = [ln(P_near) − ln(P_far)] /
   Δt_years`, raw prices, positive = backwardation = long.
5. **Trend signal:** equal-weight ensemble of {21, 63, 126, 252}-day time-series
   momentum; each horizon contributes `sign(lookback return)`; averaged to a
   direction/strength in [−1, 1], then risk-scaled.
6. **Carry form:** time-series sign of carry (primary). Cross-sectional rank is reported
   as a secondary cross-check only — not part of Go/No-Go.
7. **Portfolio:** 10% annualized vol target; 50/50 risk split trend/carry; per-instrument
   inverse-vol sizing on 60-day causal realized vol; daily rebalance; execute at next
   session's settlement (signal at close *t* → fill at close *t+1*).
8. **Costs (baseline):** per contract-side traded = $2.50 commission + 1 tick spread +
   1 tick slippage, valued at each contract's tick value. Go/No-Go requires survival at
   **2×** this baseline.
9. **Go/No-Go thresholds:** as §8 of this document — combined-book net Sharpe ≥ 0.5;
   block-bootstrap P(mean≤0) ≤ 0.05 AND DSR > 0; beats passive-beta and random-sign null;
   breadth (majority of sectors + drop-best-sector positive; majority of years +
   drop-best-year positive); modest SPX correlation with positive alpha; combined Sharpe ≥
   better single sleeve; cost-robust at 2×; then one locked hold-out confirmation.

## 13. Freeze checklist

1. §12 decisions settled and written into the doc as concrete frozen values.
2. Data pull approved (§11) — coverage + ~$2 cost accepted.
3. Doc persisted as `trend_carry/docs/PRE_REGISTRATION.md`.
4. Only then: build the minimal reusable data layer (roll-adjusted continuous + carry
   from two ranks) with the causality audit as a first-class test, pull once, and run the
   frozen protocol on development → OOS. Hold-out stays locked.
