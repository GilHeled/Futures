# Execution v1 — Batch-3 validation gate (PASS ✅)

- structural agreement (direction vs engine): **67/67**
- execution label = would_execute; n = 67  (33 human would-EXECUTE / 34 would-PASS)
- confusion (v1 vs would_execute): {'TP': 33, 'TN': 34, 'FP': 0, 'FN': 0}

## Metrics vs pre-registered criterion

| metric | value | threshold | ok |
|---|---|---|---|
| balanced accuracy | 1.0 | ≥ 0.8 | ✅ |
| false-PASS (good trades blocked) | 0.0 | ≤ 0.15 | ✅ |
| PASS recall | 1.0 | ≥ 0.8 | ✅ |
| (raw agreement) | 1.0 | — | |
| (trade kept) | 1.0 | — | |
| (over-PASS) | 0.0 | — | |

## Calibration (v1 q-bin → observed human would-EXECUTE rate)

- [0.00,0.25)  n=14  human execute rate=0.0
- [0.25,0.39)  n=20  human execute rate=0.0
- [0.39,0.60)  n=20  human execute rate=1.0
- [0.60,1.01)  n=13  human execute rate=1.0

## False-PASS cases (v1 said PASS, human would EXECUTE) — 0


## Over-PASS cases (v1 said TRADE, human would PASS) — 0

