# Is the manipulation-extreme stop genuine invalidation or too tight? (dev, engine frozen)

- trades 204 · stopped 186 · survived (never −1R) 18
- of stopped, eventual MFE ≥ 2.0R (a real move happened): 161

## Bucket classification of STOPPED trades

| bucket | all stopped | stopped w/ meaningful move |
|---|---|---|
| true_invalidation | 49 (26%) | 24 (15%) |
| premature_stop (≤12 bars) | 84 (45%) | 84 (52%) |
| late_continuation (>12 bars) | 53 (28%) | 53 (33%) |

## Excursion split around the first −1R stop (p25/median/p75)

- MFE before stop: {'n': 186, 'p25': 1.31, 'median': 2.34, 'p75': 3.81}
- MFE after stop: {'n': 186, 'p25': 1.8, 'median': 6.11, 'p75': 13.76}
- bars entry→stop: {'n': 186, 'p25': 0, 'median': 1, 'p75': 5}
- bars stop→eventual MFE: {'n': 186, 'p25': 0, 'median': 38, 'p75': 71}
- original target eventually reached after the stop: **0.478**

## Would a modestly wider stop have preserved the thesis? (premature-stop trades)

- adverse depth reached before recovery (R): {'n': 84, 'p25': 1.51, 'median': 2.25, 'p75': 4.13}
- share whose drawdown stayed within 1.5R (illustrative reference, not tuned): **0.226**

---

## Answer — the stop is NOT genuine invalidation, but "widen it" is not the fix

Of stopped trades that eventually made a real (≥2R) move (n=161): only **15% were true
invalidations**, **52% were premature stops** (thesis resumed to +2R within 12 bars), and 33% were
late continuations. The original structural target is eventually reached after the stop **47.8%** of
the time. So the manipulation-extreme stop is usually **not** a genuine invalidation point — the move
the engine identifies is real and typically continues (median MFE-after-stop 6.1R).

**So the stop is systematically too tight for HOLDING to the structural target.** But the natural fix
is not simply a wider stop: the adverse depth needed to survive a premature stop is median **2.25R**
(only 22.6% stay within 1.5R), so preserving those trades means roughly DOUBLING the stop distance —
and eating that deeper loss on the 15% that truly invalidate. That is a real risk/reward tradeoff,
not a free improvement, and optimizing it is exactly the loop we are avoiding.

**The robust response is the harvest, not a wider stop.** MFE-BEFORE the stop is median **2.34R** —
the move reliably offers ~2R in our favor *before* the pullback triggers the −1R stop. Exiting at +2R
captures that (exit study: ~50% win, +0.49R, no fat tail) and sidesteps the tight-stop problem
entirely, without taking on the doubled risk a hold-to-target stop would require.

**This reconciles both of your framings:** the stop is *not* mostly genuine invalidation (so holding
to the far target with a −1R stop is the wrong mechanical model), AND fixed-2R is a reasonable — now
mechanistically *justified* — harvesting model: it monetizes the real, repeatable ~2R excursion the
engine produces, ahead of a stop that sits inside normal path noise.

## Concrete execution rule (proposed)
Entry = engine FVG CE (unchanged). Stop = manipulation extreme, −1R (unchanged). **Exit = full exit
at +2R** (replaces the structural liquidity target as the MECHANICAL exit; the structural target
remains an analytical objective, not the exit). Execution filter v1 (P/D + CE) unchanged. Then a
single locked-OOS confirmation.
