# RESULT — Trading Study 1: Volatility-Forecast Stop/Target Adaptation (MES)

**Status: NULL (NO-GO). Development complete; hold-out never touched. — 2026-07-24.**
Durable record. The pre-registration (`PRE_REGISTRATION_STUDY1_STOP_TARGET.md`) is
preserved unchanged alongside it.

---

## 1. Question
Does the frozen, validated 30-minute realized-volatility forecast (Target A v2)
improve **executable trading performance** when used to set a dynamic ratcheting
stop + fixed take-profit, **beyond a strong naive HAR volatility estimate**, net of
realistic costs? Primary endpoint: **out-of-sample net daily Sharpe improvement,
forecast vs HAR**, corrected across the 6 pre-registered configs.

## 2. Outcome (one line)
**No.** The forecast does not beat HAR at stop/target management on either generic
strategy; the dev Go/No-Go fails decisively, and a regime post-mortem finds the
forecast−HAR effect centered on zero in every market condition tested. **NO-GO;
hold-out remains locked.**

## 3. Design (frozen; see the pre-registration)
- Frozen forecast + HAR consumed as causal OOS artifacts from `market_state` (dev = walk-forward OOS, 2020–2024). Three vol sources: `none` / `naive`=HAR / `forecast`. `D(t)=P·√V̂`, dev-only average-distance normalization (`c_naive=1`).
- Dynamic ratcheting stop (`k·D(t)` from the favorable extreme, tighten-only, bar-close updates) + **fixed** take-profit (`m·D(entry)`). Matched entries across arms (vol-source-independent calendar). Execution: next-bar-open fills, stop-first, adverse-gap fills, session-flat 15:55.
- Two generic strategies (EMA(9/21) crossover; VWAP-fade 1.5·ATR). 3 (k,m) configs → 6 forecast-arm configs. Fixed 1-contract fractional sizing. MES cost model (commission + 1-tick spread + 1-tick/side slippage).

## 4. Phase-0 verification — ALL 10 checks PASS
Artifacts causal/aligned (n=55,239); dev/hold-out boundaries enforced (guard raises); normalization dev-only (`c_naive=1`, arm means matched at 8.32 pts); stop active next-bar; tighten-only; TP fixed; stop-first; gap/slippage per spec; **entries identical across arms (1,384)**; deterministic. (`results/study1_phase0.txt`)

## 5. Development result — NO-GO
All arms are **unprofitable** (annualized Sharpe negative everywhere; every arm loses $6k–13k over 2020–2024 — the generic strategies bleed on 5-minute costs).

| strategy | config | Sharpe none / naive / forecast | ΔSharpe (fc−HAR) | boot P |
|---|---|---|---|---|
| ema_cross | C1 / C2 / C3 | −1.86/−2.10/−2.27 · −1.34/−1.61/−1.64 · −1.78/−1.47/−1.38 | −0.16 / −0.02 / **+0.09** | 0.79 / 0.53 / 0.30 |
| vwap_fade | C1 / C2 / C3 | −3.29/−3.14/−3.19 · −3.07/−2.74/−2.94 · −3.14/−3.12/−2.80 | −0.04 / −0.20 / **+0.32** | 0.56 / 0.84 / **0.092** |

Best forecast-vs-HAR improvement (vwap_fade C3, boot-p 0.092) is far from the corrected threshold (0.05/6 = 0.0083). Forecast beats HAR in only 2/6 configs; mean ΔSharpe = −0.004; all forecast-arm net PnL < 0. **Qualifying configs = 0 ⇒ NO-GO.** (`results/study1_dev.txt`)

## 6. Diagnostic (§13) — why the null
`results/study1_diagnostic.txt`: forecast and HAR range streams are highly correlated (0.95) but the forecast changes the exit on **89.5% of trades** — the arms are **not** identical. Yet the per-trade PnL difference is **directionless noise**: mean **+$0.13** vs std **$19** (t ≈ 0.69). The forecast reshuffles which trades win/lose **without a systematic edge** over HAR. Its validated but small (+2.3% QLIKE) advantage does not convert into economic value through exit management.

## 7. Regime post-mortem (exploratory, dev-only, hypothesis-generating)
`results/study1_postmortem.txt`. Matched per-trade PnL difference (forecast−HAR) decomposed by disagreement magnitude, forecast-vol tercile, realized-vol tercile, trend-vs-range day, and opening-hour vs rest:
- **Every bucket is centered on ~zero.** Largest |t| across ~13 buckets = **1.93** (trend days), below the ~3.0 needed for family-wise significance. A faint, **non-significant** tendency to help more in higher-volatility / trend conditions (forecast-vol terciles −0.52 / +0.38 / +0.54; realized-vol high +0.68; trend +0.49) is consistent with noise.
- **Caveat:** the opening-hour dimension is **vacuous** — the forecast is unavailable before ~11:30 ET (market_state 24-bar feature warmup), so no eligible trades exist in the opening hour.
- **Conclusion:** no market condition shows a systematic edge; the redundancy of the forecast with HAR *for stop management* is strong.

## 8. Interpretation
- **The forecast is economically redundant with a free HAR estimate for stop/target adaptation.** It genuinely differs from HAR (90% of exits change), but the differences carry no systematic PnL edge in any regime tested.
- This is consistent with the whole program: MES intraday is efficient; the volatility forecast is a valid *context/risk* signal, but its edge over cheap HAR is **too small to monetize** through exit management, and the base strategies have no directional edge for it to amplify.
- **Scope:** this null concerns *stop/target adaptation* on *two generic MES intraday strategies*. It does not, by itself, prove the forecast has no economic value in any use — but the regime-uniform result meaningfully lowers the prior for related channels (e.g. sizing driven by the same small advantage).

## 9. Decisions & next steps (per §13)
- **Hold-out never touched** and remains locked; the dev gate did not pass.
- **Study 2 (position sizing) is NOT auto-committed.** Any next study requires a fresh decision and a separate pre-registration, now informed by this post-mortem.
- Given the regime-uniform zero effect, **closing the trading-value line is the disciplined default**; a narrowly-scoped Study 2 would only be justified if a *pre-registered* rationale beyond this exploratory hint were articulated.

## 10. CLOSURE (explicit)
**The trading-value line for the frozen Target-A volatility forecast is CLOSED — 2026-07-24 — WITHOUT accessing the hold-out.** The locked hold-out (2025-01-01 → 2026-07-09) was never touched at any point in this study and is preserved untouched for any genuinely new, separately pre-registered future work. The closure rests entirely on development evidence:
- the forecast is statistically better than HAR at *predicting* volatility (Target A v2, validated);
- it materially changes trade management (exit differs on ~90% of trades);
- those changes produce **no systematic PnL improvement** (per-trade Δ mean +$0.13 vs std $19, t≈0.69);
- the null is centered on ~zero across every examined development regime (max |t|=1.93 < ~3.0);
- **the evidence does not justify opening another monetization study for this frozen volatility forecast at this time.**

No Study 2 was opened. This does not end trading research; it redirects it: the next project must first identify an **executable return stream with positive expectancy after costs**, and only then test whether volatility information improves it — no further overlay studies on unprofitable generic strategies.

## 11. Reproducibility
Code: `trading_value/` (`config, vol_artifacts, vol_sources, strategies, channel, metrics, study1, study1_diagnostic, study1_postmortem, phase0`). Tests: `tests/trading_value/` (9 pass). Deterministic. Results: `results/{study1_phase0, study1_dev, study1_diagnostic, study1_postmortem}.txt`. The frozen model is never re-fit; forecast/HAR are cached OOS artifacts from `market_state`.
