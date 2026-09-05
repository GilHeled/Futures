> **ROLLED BACK (2026-09-05).** The Lesson-15 5m-confirmation / persistent-POTENTIAL / locality /
> displacement-quality methodology documented below over-constrained the engine (1 trade across MNQ+MES,
> Aug 9-28). Per the owner, the execution engine was restored to the working intraday baseline `9a39694`
> (PD-array + local-structure-shift model, ~131 MNQ / ~93 MES trades). This file is kept as the RECORD of
> that investigation only — it does NOT describe the active engine. The execution-correctness fixes
> (reachability, no back-dated fills, dedup, deterministic stop/target, degenerate-stop) were retained.

# `ict_v2` fidelity-gap register — Lesson-15 sequence

**Measured against `3ecd6a1`** (`fix(ict_v2): enforce Lesson-15 SEQUENCE for a confirmed 15m reversal`).
These are **fidelity** findings, derived from the raw course material and a read-only behavioral audit on
the MNQ Aug 9–28 1m dataset. **No performance/P&L reasoning is used or implied as justification** — the Aug
numbers appear only as *where the behavior was observed*, never as *why* a change is warranted.

Both issues live in `ict_v2/pipeline.py`: `_trend_sequence` (the Lesson-15 classifier) and the audit `when`
block emitted by `execution_for_scenario`.

---

## Issue B — PRIMARY fidelity gap: the sequence is validated LOCALLY, not maintained as ORDERED STATE  ` [COURSE] `

### The exact Lesson-15 sequence (source)

Lesson 15 (שינויי מגמה תוך יומיים) defines a trend and its change structurally, not by magnitude:

> An uptrend is defined when there are **two rising highs and two rising lows**; a downtrend when there are
> two falling highs and two falling lows. When this movement ends — i.e. they **stop** creating rising
> highs/rising lows (or stop creating falling lows/falling highs) — the trend halts and a change occurs.
> In practice, the moment we see **a high that fails to break the previous high** (uptrend) — or a low that
> fails to break the previous low (downtrend) — we define that point as a **POTENTIAL** for trend change.
> **Only when we indeed see falling highs AND falling lows produced** (breaking an uptrend) — or rising
> highs AND rising lows (breaking a downtrend) — do we define that the trend has **indeed broken**.

The canonical 5-minute diagram (`screenshots/שיעור 15/שינוי מגמה - 5 דקות.png`) marks two **ordered** events:
1. `שיאים שלא שברו שיא קודם — פוטנציאל להיפוך מגמה` — highs that fail to break the prior high = **POTENTIAL**.
2. `שפל שששבר שפל קודם — היפוך מגמה` — a low that then breaks the prior low = **CONFIRMED**.

The potential forms **first**; the lower-high stays below the prior high until the prior low breaks.

### POTENTIAL vs CONFIRMED

- **POTENTIAL** = an established trend has produced a *failed-continuation pivot*: in an uptrend, a **lower
  high** (a high that failed to make a new HH); in a downtrend, a **higher low** (a low that failed to make a
  new LL). This is a *watch* state — no reversal yet.
- **CONFIRMED** = while that potential is still valid, price then **breaks the last opposing structural
  swing** — in the short case the last HL (→ LL); in the long case the last LH (→ HH). This is the diagram's
  "…שבר…שפל/שיא קודם — היפוך מגמה" marker.

### The cancellation rule (the crux)

A potential is alive **only while the original trend remains stopped**. The governing clause is *"the trend
halts only when it **stops** creating falling lows/highs."* Therefore:

> **CANCEL/RESET:** if the original trend **resumes** by making a **new structural extreme beyond the prior
> trend's last extreme `S[k-1]`** — a new lower low below the last falling low (long case) / a new higher
> high above the last rising high (short case) — the potential reversal is **invalidated** and the sequence
> must restart. A subsequent higher low / lower high is required to form a *new* potential.

This is equivalent to the source's confirmation condition read the other way: a confirmed long needs *rising
highs **and** rising lows*; if the most recent structural low is a **new lower low**, "rising lows" is false,
so no reversal exists regardless of a high having been broken.

#### Why crossing the failed-continuation pivot itself is NOT the cancellation condition

The invalidation reference is the **prior structural extreme `S[k-1]`**, *not* the failed-continuation pivot
`S[k+1]`. Briefly trading through the pivot is not a trend resumption; only a **new extreme beyond the prior
trend's last extreme** is. Case 2 (below) demonstrates this directly: price traded above the lower-high but
never above the prior high, so the uptrend never resumed and the potential stayed valid. Using the pivot
itself as the reference would wrongly cancel Case 2.

### Why the current `_trend_sequence` cannot guarantee the potential remained valid

`_trend_sequence(structural, mss, direction)` runs **at the confirmation bar** and reconstructs a *local*
shape from the skeleton indices `k-3 … k+1` around the broken swing `k`:
- it proves *a* prior trend and *a* failed-continuation pivot **existed** in that neighborhood;
- it does **not** prove that pivot was **the active structural state that survived** from its formation to the
  break. Any new extreme the prior trend printed **between** the pivot and the break is outside the
  `k-3…k+1` window and is never consulted.

So the classifier answers *"did the pattern ever exist locally?"* when the course asks *"is the potential
still valid at the moment of the break?"* Those differ exactly when the trend resumed in between.

### Demonstrated evidence (behavioral audit, read-only)

Reference = the prior structural extreme `S[k-1]`.

| confirmed reversal | prior extreme `S[k-1]` | failed-cont. pivot `S[k+1]` | broke `S[k-1]` before the break? | same 15m bar as `k`? | source verdict |
|---|---|---|---|:--:|---|
| **Case 1** — long, 2026-08-19 04:15 | low **29547.25** | HL 29559 | **yes** → to ~29435 (new lower low) | **yes** (both index 194) | **false confirmation** |
| **Case 2** — short, 2026-08-25 10:15 | high **29420** | LH 29409.25 | **no** (stayed < 29420) | no | **valid, source-faithful** |

- **Case 1** is a false long confirmation: after the HL (29559), price made materially lower lows (below the
  prior low 29547.25) for ~13 hours, then rallied back through a stale high (29609.5). Per Lesson 15 the
  downtrend resumed → the potential was already dead; the "reversal" should have been cancelled.
- **Case 2** is a genuine short reversal: HH/HL uptrend → LH (29409.25 < prior high 29420) → break the last HL
  (29302.5). The LH held below 29420 throughout, so the potential survived to the break.

### The same-bar degeneracy is a symptom, not a separate rule

In Case 1 the failed-continuation pivot `S[k+1]` (HL 29559) and the broken swing `S[k]` (high 29609.5) are the
**same 15m bar** (index 194) — a wide outside-bar the skeleton keeps as both a high and a low pivot. The
Lesson-15 sequence is inherently **time-ordered** (LH → *then* down to HL → *then* up through LH); collapsing
the pivot and the broken swing onto one bar removes the "then," yielding a temporally impossible sequence.

This is a **consequence of reconstructing a local pattern instead of maintaining ordered state** — a proper
state machine, in which the potential must form as a distinct, later pivot and be carried forward to the
break, excludes the degeneracy for free. **It is NOT a separate, arbitrary "the pivots must be on different
bars" rule** and must not be implemented as one.

### Required architectural direction  ` [COURSE] `

Replace the point-in-time local reconstruction with an **ordered, stateful** progression maintained across
15m closes:

```
established trend  ──▶  failed-continuation pivot forms  ──▶  POTENTIAL
                                                               │
      ┌────────────────────────────────────────────────────────┼───────────────────────────────┐
      │ prior-trend NEW EXTREME beyond S[k-1]        required OPPOSING STRUCTURAL BREAK           │
      │ (new LL below last falling low /             (break last HL→LL / last LH→HH)   neither    │
      │  new HH above last rising high)              while potential still valid                  │
      ▼                                              ▼                                 ▼          │
   CANCEL / RESET                                 CONFIRMED                       remain POTENTIAL │
   (await a new failed-continuation pivot)                                                        │
      └──────────────────────────────────────────────────────────────────────────────────────────┘
```

The three transitions out of POTENTIAL are **mutually exclusive** and evaluated every 15m close. This is
**`[COURSE]` structural behavior** — the literal Lesson-15 definition of when a trend has vs has not changed.
It is **not** a performance filter, an `[RES]` heuristic, or a magnitude/energy rule.

### Impact characterization (fidelity, not performance)

- The flaw is **architectural**, not a one-off: the local classifier cannot enforce potential-validity for
  *any* signal; Case 2 stayed valid by the data, not by any code enforcing it.
- In the inspected window it exposed **1 of the 2** actionable (confirmed) reversals (Case 1).
- The same prior-trend-resumed condition also appears in rejected events (premature 6/10, continuation 10/16)
  but **does not change their decisions** — they are rejected regardless — so it is decision-relevant **only**
  in the confirmed bucket.

---

## Issue A — SECONDARY audit defect: `failed_continuation_pivot` populated even when the pivot fails LH/HL  ` [AUDIT-ONLY] `

`_trend_sequence` builds the audit `detail` **before** and **independent of** the `failed_lh` / `failed_hl`
test, so `failed_continuation_pivot` is set to `"LH <px>"` / `"HL <px>"` for `structural[k+1]` **whenever that
pivot exists**, regardless of whether it actually satisfies the lower-high / higher-low relationship.

- **Required behavior:** `failed_continuation_pivot` must be `None` unless `S[k+1]` genuinely satisfies the
  relationship (short: `S[k+1] ≤ S[k-1]`; long: `S[k+1] ≥ S[k-1]`).
- **Scope:** the string is correct for both confirmed reversals but wrong for **26 of 28** rejected events in
  the audit sample (premature 10/10; continuation 15/16) — i.e. it prints a pivot label precisely where *no
  valid failed-continuation pivot exists*, which is misleading to a human reading the audit alone.
- **Explicitly audit-only / decision-harmless:** the *classification* is unaffected (those events are
  correctly `premature` / `continuation` / `reversal_state=none`). Only the human-facing audit field is wrong.

---

## Validation evidence (2026-09, read-only audit of `3ecd6a1`, MNQ Aug 9–28)

Recorded for provenance. Not used as justification for either issue.

- **No look-ahead found** in the six inspected cases: for every case, every structural pivot `_trend_sequence`
  read was *knowable* (its `confirm_index` bar close) at or before both the confirmation-break bar and the
  as-of decision cursor. The classifier is causally sound.
- **Case 2** (confirmed short, Aug 25) is source-faithful: `HH/HL uptrend → LH → break last HL → LL`, potential
  valid to the break.
- **Case 1** (confirmed long, Aug 19) is **not** source-faithful: stale potential (prior trend resumed) +
  same-bar degeneracy.
- **1 of 2** actionable confirmed reversals in the window exposed the stale-potential problem (Issue B).
- The stale/local-sequence condition also appeared in rejected (premature/continuation) events but **did not
  affect their decisions**.

---

## Proposed fix (APPROVED 2026-09) — one minimal fidelity correction  ` [COURSE] `

Issue B and Issue A are corrected together in a single touch of `_trend_sequence` (the audit surfacing is
carried by `_structural_reversal`'s existing non-`reversal` path). **Skeleton-only** — no signature change, no
15m-bars plumbing, no persisted mutable state. The engine already re-derives `confirm_ms` per 15m close, and
`confirm_ms.structural` carries the full ordered skeleton, so the `POTENTIAL → CANCEL / CONFIRM / hold` state
machine is re-derived faithfully at each close rather than persisted.

**`_trend_sequence` gains three checks:**

1. **Strict temporal ordering** — the failed-continuation pivot must form strictly after the swing it will
   break: `S[k+1].index > S[k].index`. Fails ⇒ `degenerate`. (This is the ordering the sequence *implies*
   — the HL forms after the LH — not an arbitrary "different bars" rule.)
2. **Potential-survival scan** — after a qualifying prior-trend + failed-continuation pivot, scan skeleton
   swings with `S[k+1].index < swing.index ≤ mss.confirm_index` for a prior-trend resumption **beyond the
   prior extreme `S[k-1]`**: long ⇒ any structural **low `< S[k-1]`**; short ⇒ any structural **high
   `> S[k-1]`**. Found ⇒ `invalidated`. The reference is `S[k-1]`, never the pivot itself.
     - Granularity = a **confirmed structural swing** beyond `S[k-1]` (the source's "a new falling
       low/rising high" is a pivot); **no** body-close rule is added (the course teaches none).
     - Scan bound = **`mss.confirm_index`** (the confirmation break); behavior after confirmation is a
       later event, not part of this reversal's validity.
3. **Issue A** — `failed_continuation_pivot` is emitted only when `S[k+1]` actually satisfies the LH/HL
   relation (`≤ S[k-1]` short / `≥ S[k-1]` long); otherwise `None`.

**Outcomes** grow to `{reversal, premature, continuation, invalidated, degenerate, none}`; only `reversal`
confirms. `_structural_reversal` routes `invalidated` / `degenerate` through its existing non-`reversal`
(WATCHING) path, surfacing each as its own `classification` with `reversal_state` `cancelled` / `degenerate`.

**Unchanged:** v1 (frozen); 15m scale; WHERE; ≥50% retrace; P/D side; FVG optional; stop beyond manipulation;
target ≥2R; reachability; no STALE_PROGRESS; no displacement/energy. No new thresholds/ATR/buffers.

---

## Implemented + behaviorally validated (2026-09, `7b3d768`)

`fix(ict_v2): Lesson-15 potential must survive to the break (stateful sequence)`. Full suite 114 pass;
v2 + dashboard rebuilt. **Status: IMPLEMENTED and behaviorally validated for the observed real-data cases**,
with one explicit remaining validation limitation (below).

Chart-validation set drawn blindly by chronology from the corrected build (MNQ Aug 9–28):

- **Case 2** (confirmed short, Aug 25): a genuine Lesson-15 reversal — **remained `confirmed`** (potential
  survived to the break). ✓
- **Case 1** (long, Aug 19): the previous false confirmation is now **rejected as `degenerate`** (its
  failed-continuation pivot and broken swing share the 15:45 bar; ordering fails before the survival scan). ✓
- **Premature** (long, Aug 10): **Issue A fixed** — `failed_continuation_pivot=None` where no valid LH/HL
  exists (was mislabeled `"HL 29666"`). ✓
- **No look-ahead** was found in the inspected structural sequences: every pivot the classifier reads is
  knowable (its `confirm_index` bar close) at/before the confirmation break and the decision cursor.
- The **`invalidated`** long and short paths are covered by unit tests
  (`test_seq_long_potential_invalidated_*`, `test_seq_short_potential_invalidated_*`).

### Remaining validation limitation (NOT a blocker)

No **naturally occurring `invalidated`** example was found in MNQ **or** MES Aug: the only real trend
resumption (Case 1) was *also* same-bar, so the ordering check classifies it `degenerate` before the survival
scan applies. The `invalidated` transition is therefore **unit-test-covered but not yet behaviorally validated
on real market data**. Preserved here as an explicit limitation to close later with a wider dataset — it does
not block the fix.

### WHAT status

`scale + trend sequence + potential ordering + cancellation logic + confirmation` are implemented; observed
real-data sequence cases pass current chart validation.

**This is NOT a statement that overall methodology fidelity is complete** — HOW / displacement quality
(Lesson 12 energetic-move character, displacement grading) has intentionally **not** been evaluated yet.

---

## Lifecycle fix IMPLEMENTED (2026-09) — persistent POTENTIAL state (`ReversalBook`)

The diagnostic proved the `POTENTIAL→CONFIRMED` collapse was largely a **state-retention artifact**, not genuine
Lesson-15 behavior: on the stateless 5m build, a valid POTENTIAL flickered out of the re-derived skeleton after
a **median of one 5m close** (max 25 min) while the 20h/240-bar window was nowhere near exhausted, and the
market went on to resolve **every** one of the 11 "unresolved" cases. Fix: `ict_v2/reversals.py` carries the
POTENTIAL as first-class structural state.

- Creation is still validated ONLY by `pipeline._trend_sequence` (rules unchanged, byte-for-byte).
- Identity frozen at creation = `direction + prior-trend structure + S[k] + S[k-1] + failed-continuation pivot`
  (prices tick-normalized); `created_at` is metadata, NOT identity — a rediscovery of the same identity on a
  later close is absorbed, never duplicated, never resurrected after a terminal event.
- Transitions run only on CLOSED 5m bars against the FROZEN references (never re-anchored): CANCELLED = a new
  structural swing beyond frozen `S[k-1]` (decision #2, no body-close rule); CONFIRMED = a reversal-direction
  displacement + body-close beyond the frozen `S[k]` price (evaluated against the immutable target — no
  price-match tolerance, decision #3); else remain POTENTIAL. M1 never mutates the book.
- Active POTENTIALs are NEVER evicted (decision #1) — a very high sanity limit only logs; terminal history is
  capped for memory/UI only. The lifecycle is independent of scenarios/P-D/WHERE/target/≥2R/entry; a CONFIRMED
  reversal is consumed once (marked emitted when a position opens) and never re-emitted.

### Lifecycle census (same Aug 5m data, build after this fix)

| | created | CONFIRMED | CANCELLED | active@end | rediscoveries absorbed | peak simultaneous |
|---|--:|--:|--:|--:|--:|---|
| MNQ | 11 | 2 | 9 | 0 | 32 | long 2 / short 1 |
| MES | 8 | 5 | 3 | 0 | 234 | long 1 / short 1 |

`created = confirmed + cancelled + active` reconciles exactly. **All 11 previously-"unresolved" potentials now
reach a real terminal event** (MNQ: 1 confirmed + 7 cancelled; MES: 2 confirmed + 1 cancelled) — none silently
dropped mid-life. The residual `POTENTIAL→CANCELLED` (12/19) is now **genuine Lesson-15 behavior** (the prior
trend structurally resumed beyond `S[k-1]`), not lost tracking.

### Downstream funnel under persistence (population only — no P&L)

- MNQ: 11 potentials → 2 confirmed → 1 ARMED → 1 filled (1 not in a compatible in-half scenario).
- MES: 8 potentials → 5 confirmed → 4 ARMED → 3 filled (1 not in a compatible in-half scenario).

Fills went from 0 (stateless 5m build) to 1 (MNQ) / 3 (MES) purely by retaining the Lesson-15 state to its
course-defined terminal event. Reported as population/lifecycle counts; **no P&L, win-rate, or expectancy**,
and no HOW/displacement evaluation.

---

## Locality correction (2026-09) — the confirming displacement must be the breaking leg

Chart validation of the 7 confirmed 5m reversals exposed a causal bug in `ReversalBook._confirming_chain`
(the `_advance` path): it confirmed a break using **any** reversal-direction displacement in the 240-bar
window — including one ~3h stale (MNQ 08-25: leg 29416→29209 while price was ~29220) or one that never
reached `S[k]` (MES 08-11/08-12). This is deterministic/causal, not a quality/energy question, so it is fixed
without any invented threshold.

**Rule (from L12/L15/L16 — the energetic move *is* the break):** a confirmation requires a reversal-direction
displacement that (a) **spans** the frozen `S[k]` (starts on the origin side, ends BEYOND it — a leg ending
before `S[k]` fails) AND (b) whose span **contains the confirmation bar** (the current closed 5m bar body-
closing beyond `S[k]`). A stale/earlier same-direction displacement, or one at another price band, no longer
qualifies. The born-confirmed path (creation with a v1-confirmed MSS) already uses the MSS's own linked
displacement and is unchanged; its chain is annotated with the same locality audit.

Audit added on confirmation: displacement start/end price+time, frozen `S[k]`, confirmation bar time,
`spans_s_k`, `confirm_bar_belongs`; and on a blocked break, `locality_reject.reason`.

**Effect on the Aug 5m population (population only — no P&L):** confirmed reversals **7 → 3**
(MNQ 2→1, MES 5→2); the 4 stale/non-spanning confirmations now resolve as CANCELLED. The 3 survivors are the
born-confirmed cases with local, spanning displacements. Fills 4 → 1. **Locality does NOT address the
energetic/quality question** — e.g. MNQ 08-13 still confirms on flat chop (its displacement is local but tiny)
— that remains the deferred HOW work. +4 locality tests (local confirm; stale-earlier reject; ends-before-
`S[k]` reject; other-regime reject). 127 pass.

---

## Displacement quality — Lesson-12 relative candle expansion (2026-09) — METHODOLOGY BASELINE FROZEN

The v1 displacement detector accepts any `net > 0` move after a sweep; the source requires the energetic move
to contain *"very large candles relative to the candles that preceded them"* (L12). One concept added — nothing
else stacked (no speed/overlap/FVG/continuation) — in the v2 confirmation (v1 frozen):

> A confirming displacement qualifies only if `max |close-open| over the confirming displacement leg` is
> **strictly greater than** `max |close-open| over the candles of the immediately-preceding minor leg`
> (bounded by v1's own width-1 minor pivots). Applied to BOTH confirmation paths (advance + born-confirmed).

`[RES]` calls (documented): candle size = **body** `abs(close-open)`, not full range (momentum vs rejection);
comparison set = the **immediately-preceding minor leg** (width-1 pivots, no fixed-N lookback). No ratio,
multiplier, ATR, percentile, points/ticks, candle-count, FVG, speed, or overlap rule. Locality preserved (the
qualifying leg must still span `S[k]` and contain the break). Audit: `disp_max_body`, `preceding_leg_max_body`,
`preceding_leg_from/to`, `expands`, `quality_basis="body max vs preceding minor-leg body max [RES]"`; and
`quality_reject.reason` when a local leg lacks expansion. +2 tests (expansion confirms; no-expansion rejects).

### Result on Aug 5m data (population UNCHANGED — the ordinal rule is faithful, not aggressive)

The gate rejects a displacement candle no larger than its preceding leg, but the course supplies **no
magnitude**, so a *marginally* larger candle qualifies. All 3 post-locality confirmations still pass:
MNQ 08-13 body 7.75 > 5.75 (chop, but larger); MES 08-20 3.75 > 3.0; MES 08-24 2.25 > 0.5. So:

- **Population (book census):** MNQ 11 potentials → 1 confirmed / 10 cancelled; MES 8 → 2 / 6. Total
  **19 → 3 confirmed**, 16 cancelled, 0 active.
- **Trades (book.trades, frozen 5m build):** MNQ 0, MES 1 = **1 trade** over Aug 9–28.
- **P&L checkpoint (diagnostic, tiny n):** the 1 trade (MES 08-20 long) **STOPPED: −1R / −$50 @ $50-risk;
  MNQ 0 trades.** *(Correction 2026-09-05: an earlier note here said "3 trades / +0.3R"; that came from a
  stale runner that invoked `V2Live("4H","1H","15m","1m")` — passing 15m as the confirm TF positionally, i.e.
  the OLD 15m confirmation, not this frozen 5m build. `V2Live()` defaults, and the funnel audit, give 1 trade.)*

The remaining chop concern (a *small* displacement that is nonetheless larger than its preceding leg) is an
**absolute-magnitude** question the course does not gate — deliberately NOT added.

### FREEZE

The v2 Lesson-15 methodology (5m confirmation · 15m liquidity/context · persistent POTENTIAL lifecycle ·
locality · relative candle expansion) is the **frozen baseline**. No further methodology tightening unless a
real implementation bug or a direct course contradiction is found. Remaining open item, unchanged and NOT
gating: whether a 5m HH/HL can form inside very small chop (structural-scale question).
