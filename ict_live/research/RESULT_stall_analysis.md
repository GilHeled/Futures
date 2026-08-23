# Why does price stall ~2R? — characterization (dev, engine frozen; diagnostics only)

Trades: 204. stop == displacement start (manipulation extreme) in 100.0% of trades.

## Distributions (p25 / median / p75)

| metric | p25 | median | p75 |
|---|---|---|---|
| mfe_R | 3.29 | 8.19 | 14.73 |
| disp_size_R | 2.52 | 3.67 | 5.09 |
| entry_frac_f | 0.2 | 0.27 | 0.4 |
| dist_entry_to_dispEnd_R | 1.52 | 2.67 | 4.09 |
| mfe_vs_dispEnd | 1.28 | 2.99 | 5.92 |
| dist_opp_liq_R | 0.23 | 0.57 | 1.52 |
| risk_ATR | 0.48 | 0.73 | 1.0 |
| mfe_ATR | 2.8 | 5.22 | 8.88 |

## Where does the MFE stall relative to the impulse high (displacement end)?

- below it (<0.8×): 29
- AT it (0.8–1.2×): **13**  of 201
- beyond it (>1.2×): 159

## H1 — is the stall a genuine opposing pivot?
- MFE bar forms an opposing pivot in **84.0%** of trades

## H3 — proportionality vs volatility
- corr(MFE_R, disp_size_R) = 0.285  (structural proportionality if high)
- corr(MFE_ATR, risk_ATR) = 0.145

---

## Interpretation — the "2R stall" is NOT a market property

**Critical distinction in how MFE is measured:**
- Exit study (earlier): MFE bounded by the EXIT bar (once the −1R stop closes the trade, we stop
  looking) → median **2.46R**.
- This study: MFE over the FULL horizon, IGNORING the stop → median **8.19R**.

The gap is the whole story: **price does not stall at 2R.** Ignoring the stop, favorable excursion
runs a median ~8R (5.2 ATR), goes ~3× beyond the displacement's own impulse high (mfe_vs_dispEnd
median 2.99; only 13/201 stall AT the impulse high, 159 blow past it), and the extreme forms a
genuine opposing pivot **84%** of the time — far beyond 2R. So the structural move typically extends
well past the engine's ~6R target and completes much later.

**Verdict on the four hypotheses (why price appears to "stop" near 2R):**
- **H1 structural-complete at 2R — NO.** The real opposing pivot forms at median ~8R, not 2R.
- **H2 opposing liquidity ~2R — NO.** Nearest opposing structural swing is median 0.57R (right above
  entry); the engine's target liquidity is ~6R. Neither is 2R.
- **H3 volatility exhaustion — NO.** No fixed-ATR stall (corr(MFE_ATR, risk_ATR)=0.15); eventual MFE
  is a large 5.2 ATR.
- **H4 entry geometry — PARTIAL/coincidental.** Entry sits median 27% into the impulse (entry_frac_f
  0.27), so returning to the impulse high is ~2.67R (dist_entry_to_dispEnd_R median) — near 2R by
  construction. But price does not stop there; it continues ~3× further.

**The real explanation:** the ~2R optimum from the exit study is a STOP-INTERACTION artifact, not a
market stall. The stop (manipulation extreme) is tight — median 0.73 ATR — while the eventual move is
~5 ATR. So most trades take a −1R hit on the drawdown path BEFORE the large move matures; only ~50%
reach +2R before −1R. A 2R target "wins" because it harvests that coin-flip before the tight stop
knocks the trade out — NOT because price stalls at 2R.

**Consequence:** the hypothesis is NOT confirmed. Adopting a fixed 2R exit would treat a symptom of
the stop/target/drawdown interaction, not a structural property. The engine's structural target is a
reasonable ANALYTICAL objective (price reaches ~8R median eventually); the binding constraint is the
tight stop vs the move's drawdown path. This REOPENS, rather than closes, the exit question — and it
does so with a clearer mechanism. No freeze recommended on this evidence.
