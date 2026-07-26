# RESULT — Overnight Strategy: CLOSED (look-ahead artifact)

**Status: CLOSED — 2026-07-26. The overnight edge was a look-ahead artifact; it
does not survive a strictly-causal re-measurement. No live, no spend.**

---

## 1. What happened
The overnight h10 EV strategy (MYM/M2K/MES) — recorded across Rounds 13–14 as *"the strongest, most thoroughly de-risked result in the project,"* surviving net-of-cost gap-aware fills, two losing null benchmarks, multi-period temporal robustness + drop-best, and a frozen-model OOS reproduction — is **not real**. A causality audit (Phase A of the pre-registered re-validation) found a look-ahead leak, and the corrected implementation has **no edge**.

## 2. The leak
`mnq_system/modeling/features.py::build_feature_matrix` computes **`gap_atr_ratio`** and **`overnight_imbalance_pctile`** per calendar date and stamps them on *every* bar of that date. Both encode that day's **09:30 ET session open** (the gap = 09:30 open − prior close; the imbalance window ends at 09:30). Every bar **before 09:30 ET** — the overnight/pre-open window where this strategy trades — therefore reads **future** information. `gap_atr_ratio` for an overnight bar is ≈ the overnight move the model is trying to predict.
Prefix-stability proof: at a 04:40 ET bar, `gap_atr_ratio` = −4.47 on the full series but **NaN** computed causally (the open hasn't printed).

## 3. Definitive causal re-measurement
The two features cannot be made causal in the overnight window (they *are* future info there), so the strictly-causal model excludes them and is **retrained from scratch on the 9 remaining causal features**; every overnight bar is still scored, on causal information only. Sequential `ShadowBook`, frozen train 2022-12-31, OOS 2023-2026, net of cost:

| | LEAKED (11 feat) | CAUSAL (9 feat, retrained) |
|---|---|---|
| MYM | +0.719 R (n=817) | **−0.105 R** (n=279) |
| M2K | +0.793 R (n=579) | **−0.247 R** (n=322) |
| MES | +1.014 R (n=320) | **no tradeable signals (n=0)** |

The causal model is negative in nearly every year for MYM/M2K and finds nothing for MES. The entire apparent edge came from the leak.

## 4. Why the "full rigor stack" missed it
The leak is **upstream** of everything that was tested. The null benchmarks (overnight-hold, random) don't use the model, so they never carried the leak — "the model beats the benchmarks" was purely the leaked feature. Temporal robustness, drop-best-window, and the frozen-OOS reproduction all ran *on top of* leaked features, so they measured a contaminated signal consistently, not a real one. **Feature-level causality was the one thing none of them checked.** That is the enduring lesson.

## 5. Decision (pre-agreed, evidence-based)
Per the frozen Phase-A gate and the pre-agreement that we accept whatever the corrected run shows: **the overnight project is CLOSED**, as a look-ahead null — the same disciplined ending as the volatility-monetization line. The prior Rounds 13–14 "validation" is **retracted**; it is historical evidence of a bug, not of an edge.

## 6. Codebase note (required if ever reused)
`mnq_system/modeling/features.py` contains a genuine look-ahead (`gap_atr_ratio`, `overnight_imbalance_pctile` use the same-day 09:30 event for pre-open bars). Any future use of this feature module for an overnight/pre-open strategy MUST first make these strictly causal (undefined until the window completes) — or the same artifact recurs.

## 7. Standing
Both monetization leads are now closed: volatility-forecast monetization (redundant with HAR) and the model-driven overnight edge (look-ahead artifact). No instrument, no channel, has produced a real, causally-clean, cost-surviving edge in this repository. Any next attempt starts from a genuinely new economic premise, with **feature-level causality auditing built in from the start** — the check that would have saved this entire line months ago. No spending, no trading.
