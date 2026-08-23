# Baseline expectancy — engine + execution v1 (dev, historical replay)

| group | n | filled | win rate | expectancy R | total R | avg MFE | avg MAE |
|---|---|---|---|---|---|---|---|
| ALL | 273 | 204 | 0.158 | 0.21 | 42.59 | 3.84 | 1.68 |
| v1 TRADE | 184 | 137 | 0.146 | 0.09 | 12.33 | 3.69 | 1.71 |
| v1 PASS | 89 | 67 | 0.182 | 0.458 | 30.26 | 4.15 | 1.61 |

**v1 filter edge (TRADE − PASS expectancy): -0.368 R**

## By direction

| dir | n | filled | win rate | expectancy R | total R |
|---|---|---|---|---|---|
| LONG | 124 | 87 | 0.161 | 0.233 | 20.23 |
| SHORT | 149 | 117 | 0.155 | 0.193 | 22.36 |

## By symbol

| sym | n | filled | win rate | expectancy R | total R |
|---|---|---|---|---|---|
| MES | 126 | 96 | 0.177 | 0.177 | 17.0 |
| MNQ | 147 | 108 | 0.14 | 0.239 | 25.59 |

---

## Robustness (the headline expectancy is NOT robust)

- Median trade R = **−1.0**; outcome mix = 171 STOP / 31 TARGET / 1 other (203 scored).
- **Top-5 winners = 81.9R of 42.6R total (192%)** → without ~5 trades over 5.7 years, the system is net negative.
- Expectancy with each trade **capped at +5R → −0.149R** (v1 TRADE −0.229, v1 PASS +0.016).
- Costs/slippage NOT included (would only reduce these).

## Honest conclusions (first baseline)

1. **Structural engine "makes money"? NOT established.** Positive raw expectancy is entirely driven
   by a handful of large target hits (fat-tailed lottery profile); median/capped it is negative. This
   is the same outlier-fragility pattern that sank earlier research lines — not a demonstrated edge.
2. **Does execution v1 improve P&L? NO** (filter edge −0.37R raw, negative under every cut). v1 was
   validated as a FIDELITY filter — it faithfully reproduces which setups the trader would execute —
   but trader-fidelity is SEPARATE from profitability, and here favorable-P/D-location entries did
   not outperform. This is consistent with the long-standing rule that fidelity and outcome are
   different questions; it does not invalidate v1's fidelity role.
3. The MVP now does its job: it turns the engine into something measurable, and its first measurement
   is a sober one — the fixed single-target/stop-at-manipulation-extreme trade definition does not
   show a robust mechanical edge on dev. The lever (if any) looks like the PAYOFF structure (targets/
   management), not the entry filter — but that is a deliberate, separate decision, not to be started
   as an optimization loop without the user.
