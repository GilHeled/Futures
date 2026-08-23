# Exit analysis — accepted TRADEs (dev, engine frozen; diagnostics only)

## TRADE  (n=137, stopped=117)

- target hit ratio (before stop): **0.139**
- reached before stop: 1R 0.511 · 2R 0.387 · 3R 0.263
- reward_R (target distance) distn: {'min': 3.04, 'p25': 4.04, 'median': 5.96, 'p75': 9.36, 'p90': 16.09, 'max': 107.29}
- MFE_R distn: {'min': 0.33, 'p25': 1.35, 'median': 2.46, 'p75': 4.37, 'p90': 8.02, 'max': 32.41}
- MFE buckets: {'[0,0.5)': 7, '[0.5,1)': 21, '[1,2)': 30, '[2,3)': 26, '[3,99)': 53}
- ENTRY — moved >=0.5R: 0.949 · moved >=1R: 0.796
- MANAGEMENT — of STOPPED trades, reached +1R MFE first: 0.761 · +2R: 0.504
- timing (bars): to-MFE {'min': 0, 'p25': 0, 'median': 0, 'p75': 1, 'p90': 15, 'max': 43} · to-stop {'min': 0, 'p25': 0, 'median': 1, 'p75': 3, 'p90': 11, 'max': 93} · to-target {'min': 0, 'p25': 8, 'median': 24, 'p75': 45, 'p90': 69, 'max': 95}

## PASS  (n=67, stopped=54)

- target hit ratio (before stop): **0.179**
- reached before stop: 1R 0.612 · 2R 0.433 · 3R 0.269
- reward_R (target distance) distn: {'min': 3.03, 'p25': 4.18, 'median': 5.27, 'p75': 7.58, 'p90': 12.24, 'max': 44.92}
- MFE_R distn: {'min': 0.35, 'p25': 1.54, 'median': 2.58, 'p75': 4.76, 'p90': 8.18, 'max': 43.5}
- MFE buckets: {'[0,0.5)': 1, '[0.5,1)': 10, '[1,2)': 14, '[2,3)': 16, '[3,99)': 26}
- ENTRY — moved >=0.5R: 0.985 · moved >=1R: 0.836
- MANAGEMENT — of STOPPED trades, reached +1R MFE first: 0.796 · +2R: 0.537
- timing (bars): to-MFE {'min': 0, 'p25': 0, 'median': 0, 'p75': 6, 'p90': 20, 'max': 65} · to-stop {'min': 0, 'p25': 0, 'median': 1, 'p75': 10, 'p90': 22, 'max': 68} · to-target {'min': 0, 'p25': 15, 'median': 25, 'p75': 64, 'p90': 76, 'max': 88}

---

## Answers to the three questions (dev, n=137 accepted TRADEs)

**1. Are entries bad? NO — entries are good.**
- 94.9% of trades moved ≥0.5R in the intended direction; 79.6% moved ≥1R.
- Median MFE = 2.46R; only 7/137 (5%) barely moved (<0.5R).
- The engine identifies locations where price does move the intended way. Entry quality is NOT the problem.

**2. Are targets too ambitious? YES — the exit model is the problem.**
- Target hit ratio (before stop) = **13.9%** — the fixed distant-liquidity target is reached ~1 in 7.
- Target distance median = 5.96R (p90 16R, max 107R), while MFE median is only 2.46R — the target
  sits ~2.4× beyond where price typically runs.
- This directly explains the fat tail: the only wins are the rare full runs to a far pool (paying up
  to +107R at 14% frequency); everything else stops at −1R. The distribution is a mechanical artifact
  of the target choice, not of entry selection.

**3. Would management convert the distribution? STRONG yes-signal.**
- Of STOPPED trades, **76.1% had already reached +1R MFE** and **50.4% had reached +2R MFE** before
  reversing to −1R. Reached-before-stop: 1R 51%, 2R 39%, 3R 26%.
- i.e. half of all losers were up ≥2R first. Partial exits / breakeven / trailing would convert a
  large share of the −1R outcomes into scratches or small wins — plausibly turning a fat-tailed,
  outlier-dependent curve into a steadier one.

**Cross-check:** v1 TRADE and v1 PASS are near-identical on every exit metric (target-hit 0.14 vs
0.18, MFE median 2.46 vs 2.58, stopped-reached-2R 0.50 vs 0.54) — confirming the P/D-location filter
is a fidelity signal, not an exit-quality one. The lever is the exit model, uniformly.

**Verdict:** entries good, target unrealistic, management is the untested lever. Any strategy change
should target the EXIT, not the entry — and only as a deliberate, pre-registered study.
