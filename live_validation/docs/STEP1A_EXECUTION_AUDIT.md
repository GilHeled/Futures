# Step 1a — Overnight Edge: Execution-Assumptions Audit (free, offline)

> **SUPERSEDED — 2026-07-26.** The edge analyzed here was subsequently found to be a **LOOK-AHEAD ARTIFACT** (`gap_atr_ratio` reads the future 09:30 ET open on pre-open bars). See `PHASE_A_RESULT.md` and `RESULT_OVERNIGHT_CLOSED.md`. The sensitivity numbers below were computed on a contaminated model and do **not** reflect a real edge. Retained as honest history.

**Scope:** MYM / M2K / MES short-horizon (h10) overnight edge, frozen model
(`bundles/`, trained to 2022-12-31), evaluated OOS 2023-01 → 2026-07 on cached
data only. No live feed, no spending. Verified faithful: the fast sensitivity
sweep reproduces the real `shadow_book.ShadowBook` byte-for-byte
(MYM 817 trades / +0.719R, M2K 579 / +0.793R, MES 320 / +1.014R @3t).

Sensitivity data: `live_validation/results/execution_sensitivity.txt`.

---

## Q1. What exactly are the execution assumptions?
Extracted from `shadow_book.py` / `inference.py` / `bundle.py`:
- **Entry:** on an *off-hours* bar (outside RTH trading windows, not session-ending) where the frozen EV signal crosses its cost hurdle (rising-edge, 10-bar debounce). Fill at that **5-minute bar's close ± fill-slippage** (default **3 ticks/side**).
- **Protective stop:** `close − 1.5·ATR(14)·direction`, sized on the (RTH-scaled) ATR.
- **Exit (dynamic EV, gap-aware), first of:** (a) **stop** — gap-aware: fills at the *worse* of stop price and the bar's open; (b) **EV reversal** — when EV flips against the position, exit at the **next bar's open**; (c) 500-bar max-hold. Exit fill ± fill-slippage.
- **Costs:** commission **$1.50 round-trip**; slippage as above. Net R = `((exit−entry)·dir·$pt − commission) / (|entry−stop|·$pt)`.
- **Decoupling (important):** the entry *signal* is gated by a **1-tick** cost hurdle (reproduces the research calendar); the **3-tick** fill assumption is applied only to fills. So the entry set is invariant to fill slippage — which is what makes the sensitivity below a clean, one-variable sweep.
- **No overnight flatten** — the position is *held through* the session boundary to its own EV/stop exit (this is the overnight strategy, by design).

## Q2. Which assumptions are critical to profitability?
Ranked by how much the edge depends on them:
1. **That the modeled fills are achievable in the thin overnight micros** — entry at the 5-min close and EV-reversal exit at the *next 5-min open*, at ~3 ticks. This is the load-bearing assumption. (Everything else is downstream of it.)
2. **That fill quality is roughly uniform across trades** — the backtest applies a *fixed* per-tick slippage to every trade regardless of the size/speed of the overnight move.
3. **Gap-aware stop realism** — already modeled (worse of stop/open); residual risk is intrabar gaps beyond the 5-min open on thin bars.
4. Commission level — immaterial (see Q3; the edge tolerates many multiples of it).

## Q3. Sensitivity to slippage / spread / missed fills
**Slippage / spread — the edge is remarkably ROBUST.** Net avg R per trade vs per-side fill slippage (entries fixed), frozen OOS:

| ticks/side | 0 | 1 | 2 | **3** | 4 | **5** | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|
| MYM | +1.04 | +0.92 | +0.81 | **+0.72** | +0.63 | **+0.56** | +0.48 | +0.42 | +0.36 |
| M2K | +1.13 | +1.00 | +0.89 | **+0.79** | +0.70 | **+0.62** | +0.55 | +0.48 | +0.41 |
| MES | +1.45 | +1.29 | +1.14 | **+1.01** | +0.90 | **+0.80** | +0.71 | +0.62 | +0.54 |

Degradation is ~0.08–0.11 R per extra tick; **all three stay strongly positive at 8 ticks/side** (bootstrap P(mean≤0)≈0 at 3t and 5t). A realistic overnight micro spread is ~1–2 ticks. **Average slippage/spread is not the binding constraint** — there is enormous headroom.

**Missed fills:** if misses are *random*, average R is unchanged and only total P&L scales — benign. The real exposure is **adverse** misses (see Q4).

## Q4. Maximum execution deterioration before the edge disappears
- **On the average-slippage axis:** effectively unbounded within any realistic range — **breakeven is beyond 8 ticks/side** (≈ $8 round-trip for MYM/M2K, ≈ $20 for MES) for all three. The edge cannot plausibly be killed by average fill cost.
- **On the adverse-selection axis (the real risk):** the edge is **concentrated in its right tail** — removing the best ~**5–6%** of trades drives net avg R to zero. So the edge survives *any* average-cost deterioration but **not** a scenario where fill quality is *systematically worse on the biggest overnight winners* (e.g. the large moves that drive the edge coincide with the widest spreads / thinnest books / gap-through prints, so both entry and exit fill far worse exactly on the trades that matter). The fixed-per-tick backtest cannot see this correlation; it assumes uniform fill quality.

**Net:** the binding execution question is **not** "how many ticks of average slippage" (huge tolerance) but **"are the tail-winner trades actually fillable at modeled prices in the thin overnight micros?"**

## Q5. What can be validated offline vs requires live execution
- **Settled offline (this audit):** average-slippage/breakeven headroom (huge); gap-aware stop cost (small); tail-concentration / adverse-selection tolerance (~5–6%); faithful reproduction of the frozen edge.
- **Needs data we don't have — historical top-of-book quotes (Step 1b, small one-time pull):** the *actual* overnight bid/ask spread at entry/exit instants (near-certainly within the large tolerance), and a first read on **liquidity/spread on the big-winner exit bars** specifically.
- **Only resolvable live (Step 1c, paper/shadow via IBKR):** whether *your* order fills at the next-bar open during a fast overnight move, and whether fill quality is correlated with move size — i.e. the Q4 adverse-selection question. This is exactly what the dual-fill (`expected` vs `crossing`) shadow log measures.

---

## Recommendation for the Step 1a → 1b/1c decision
- **The edge is technically sound and execution-robust to average costs** — it clears the "still technically sound" bar decisively.
- Because average slippage has such large headroom, **Step 1b's value shrinks to one specific question: liquidity on the tail-winner bars.** A *targeted* historical quote pull (only the entry/exit minutes of the largest-R trades) would answer it cheaply; a broad quote pull is not needed.
- Given that the deepest uncertainty (fillability + correlation of fill quality with winners) is **only** answerable live, a defensible alternative is to **skip the broad Step 1b and go to a pre-registered live paper shadow (IBKR)**, which measures spread *and* fillability directly via the existing dual-fill harness.

## Open reconciliation item (pre-live)
Current-code frozen-OOS counts/edges (MYM 817/+0.72, M2K 579/+0.79, MES 320/+1.01 @3t) are **stronger but fewer-trade** than a 5-day-old note (~1229/+0.63 etc.). Verified against the live `ShadowBook`, so it is the current system's true output — but the *why* (OOS window vs a code change to features/debounce since the note) should be reconciled before committing live, purely for provenance hygiene. It does not change any conclusion above.
