# UNRESOLVED_DECISIONS — ict_live (approve before any encoding)

Every discretionary/mechanization choice, labelled `[COURSE]` / `[NEC]` (necessary
mechanization — a number the course doesn't give but the code needs) / `[RES]` (research
choice — genuinely open, never selected by P&L). Proposed defaults are **starting
hypotheses**, not fitted values. **Nothing here is encoded until you approve it.**

Legend: 🟢 already course-resolved in `ict_faithful/SPEC.md` (carry forward) · 🔴 OPEN,
needs your decision · ⭐ MAJOR (most shapes engine behavior).

---
## A. Already resolved against the course (carry forward from `ict_faithful`) 🟢
These were decided with course citations during the fidelity work; I propose reusing them
unchanged. Flag any you want reopened.
| # | Item | Resolution | Label |
|---|---|---|---|
| A1 | FVG geometry | bull high₁<low₃ ; bear low₁>high₃ | [COURSE] |
| A2 | FVG detector | purely geometric; displacement is a setup-stage filter, not part of the FVG def | [COURSE] |
| A3 | FVG mitigation | body **close** through the **far** boundary (interp A) | [COURSE]-grounded |
| A4 | FVG eligibility (multi) | must sit in the correct Premium/Discount zone | [COURSE] |
| A5 | Displacement→FVG boundary | scan the impulse that creates the MSS; FVG may complete at k+1 (cand. B) | [COURSE]-grounded |
| A6 | Arming | at FVG formation (k+1), NOT at MSS close; fill on a strictly later retrace | [COURSE] |
| A7 | Manip-extreme | running max/min across sweep→MSS; kept separate from pool price & sweep bar | [COURSE] |
| A8 | Geometry invariants | entry below/above manip-extreme; stop beyond it; invalid ⇒ reject (never adjust) | [COURSE] |
| A9 | Premium/Discount | Fib 50% of the (selected) dealing range | [COURSE] |

## B. Major decisions — B1–B4 FROZEN (user-amended 2026-07-28); B5–B11 still OPEN

### B1 ✅ FROZEN — Timeframe roles (multi-TF, signal-TF explicit) `[RES:exec_tf]`
NOT "1H setup + 5m refine." Frozen roles:
- **Weekly / Daily / 4H** = higher-timeframe context and major liquidity.
- **1H** = primary structural / liquidity analysis timeframe.
- **15m** = valid intraday structure/liquidity TF, and the **lowest TF on which significant
  swing/liquidity points are normally defined**.
- **5m** = optional intraday reversal/entry refinement *where appropriate*.
- Do **not** require a 5m confirmation if the setup is already valid on the methodology's
  active timeframe; do **not** use 5m to increase trade count.
- **Every candidate & recommendation records its explicit signal timeframe.**
- Remains configurable across faithful {1H / 15m / +5m-refine} behaviors; **never chosen by
  P&L** — compared for fidelity later.

### B2 ✅ FROZEN — Active dealing-range selector `[RES:range_selection]`
Range = the **most recent meaningful completed structural swing leg on the analyzed TF**
(not a fixed lookback, not whichever range makes P/D favorable):
- bullish range = significant confirmed **Swing Low → Swing High**; bearish = significant
  confirmed **Swing High → Swing Low**; the swings must be **structural points that produced
  a meaningful directional move**, not minor pivots.
- Premium = upper half, Discount = lower half, Equilibrium = 50%.
- HTF ranges remain **context** for LTF ranges; a new range replaces the old **only after a
  new meaningful structural leg confirms**.
- **Nested legitimate ranges are preserved hierarchically** W→D→4H→1H→15m (not silently
  collapsed); every range keeps its **source timeframe**; the engine **reports which range
  the setup uses and why**.
- "Significant swing" quantitative definition = `[NEC:sig_swing]` (see §C), kept explicit.

### B3 ✅ FROZEN — Manipulation candidate ranking `[RES:manip_ranking]`
NOT "first qualifying sweep wins." Maintain **multiple active sweep/manipulation candidates**;
a **later, stronger** candidate may **supersede** an earlier weak sweep. **Phase-1 ranking is
explicit LEXICOGRAPHIC / rule-based, NOT a learned score, and no hidden weights.** Evidence
exposed per candidate; priority order:
1. **Liquidity significance** — HTF ERL before minor/internal; named (PDH/PDL, PWH/PWL,
   Asia/London H/L, clear equal H/L, significant 15m+ swings).
2. **Location** — BSL sweep in **Premium** stronger for short; SSL sweep in **Discount**
   stronger for long.
3. **AMD context** — does it plausibly represent *manipulation* vs ordinary continuation?
4. **Displacement quality** — clear energetic move away from the swept liquidity?
5. **Causal MSS** — did that same displacement produce the relevant structure shift?
6. **Same-leg FVG** — did the MSS-causing displacement leave a valid, still-actionable FVG?
7. **HTF context** — Daily/4H alignment raises confidence but is **not** a hard veto.
8. **Target availability** — is there a meaningful opposing liquidity objective?
Store **every rejected/superseded candidate + reason**. Ambiguous ⇒ NO-TRADE.

### B4 ✅ FROZEN — Target / minimum R:R `[RES:target_rule]`
Primary target = **next meaningful liquidity objective**; setup actionable **only if that
objective offers ≥ 1:3** from the proposed entry + structural stop. `min_rr = 3.0`.
- Compute R:R to the **actual liquidity destination**; if nearest meaningful target <3R →
  **reject**; if >3R → **keep the liquidity target** (do not truncate to exactly 3R).
- Valid target hierarchy: opposing significant ERL · PDH/PDL · PWH/PWL · active Asia/London
  H/L · equal highs/lows · significant 15m+ swing liquidity · relevant HTF objectives.
- **IRL (FVG / NWOG / ORG) = intermediate reaction areas**, not an automatic replacement for
  the primary external-liquidity target unless context makes it the intended destination.
- `fixed_3R` = separate **research mode** only.

### B5–B11 — still OPEN (to resolve after §C, before freeze)
disp_leg `[RES]` · mss_rule `[RES]` · amd_phase `[RES]` · fvg_entry_loc `[RES]` ·
fvg_tiebreak `[RES]` · htf_labeling `[RES]` · quality_grade `[RES]`. (B3/B4 above now
partly constrain B5/B6; I'll bring these individually after the §C numbers are settled.)

### B5 ✅ FROZEN — Displacement-leg definition `[RES:disp_leg]` (structure only)
Multi-bar (not single-bar). Fixes the leg's **structural extent** only; the displacement
*quality* threshold stays unresolved & separate (ex-C6 — no ATR/body number frozen).
- **START = true manipulation extreme** (highest high of the buy-side manipulation for a
  short / lowest low of the sell-side manipulation for a long). The swept ERL pool and the
  manipulation extreme are kept as **separate stored objects**.
- **END = the directional impulse extreme immediately before the first meaningful
  counter-move** (impulse exhaustion), NOT the MSS-confirming close. Structure:
  `manip extreme → energetic impulse → MSS occurs INSIDE the impulse → impulse extreme →
  counter-move`. (Captures FVGs completing before/on/after the MSS candle — candidate-B, A5.)
- **Counter-move detection = first confirmed width-1 minor counter-pivot** `[NEC]` (not
  first opposite-close — a single counter-close can be noise). This width-1 pivot defines
  impulse exhaustion ONLY; it does **not** make that pivot a significant swing / ERL /
  dealing-range endpoint / MSS swing (those stay separate concepts).
- **CAUSALITY (hard rule + mandatory test):** the leg endpoint is known only once the
  counter-pivot is confirmed (needs later bars). The leg may be tracked *provisionally* as
  it develops, but no entry may use information not yet knowable at its timestamp. FVGs are
  registered causally at 3rd-candle close; MSS confirmed at its required close; a setup must
  **not** retroactively claim a retracement that occurred before the leg endpoint was
  knowable. Explicit **prefix-stability / no-look-ahead unit test** required.
- **Same-leg FVG =** correct polarity · forms causally after the manipulation begins ·
  belongs to the directional displacement that causes the MSS · middle candle inside the
  **finalized** displacement leg · still valid/unmitigated when eligible for entry. The
  three-candle FVG definition is **not** loosened.

### B6 ✅ FROZEN — MSS "meaningful break" rule `[RES:mss_rule]`
- **Structural swing that must break** (not merely "last confirmed swing"): the most recent
  confirmed **structural** opposing swing that defines the **pre-manipulation** structure —
  it must belong to the active analysis TF, have participated in the current structure,
  exist **before the manipulation begins**, and represent the structure being reversed.
  NOT a width-1 internal pivot, NOT a remote historical fractal, NOT a minor fluctuation.
  Store: swing timeframe · degree · timestamp · **why it was selected** as the structural swing.
- **Break by body CLOSE** beyond that swing (wick-only penetration is insufficient).
- **Three states:** **Potential MSS** (price approaching/interacting with the structural
  swing, no break yet) → **Candidate MSS** (a candle closes beyond it, not yet validated as
  belonging to the manip→displacement sequence) → **Confirmed MSS** (candidate shown to
  belong to the same displacement leg originating from the manipulation extreme, satisfying
  B5 causality). **Only Confirmed MSS advances the setup.**
- **Degree = the candidate's active signal timeframe** (recorded per B1); a 1H candidate uses
  1H structure, a 15m candidate uses 15m — do not force 15m on everything.
- **`mss_acceptance_state`** (`pending` / `accepted` / `weak_acceptance`): a **diagnostic-only**
  field capturing "acceptance beyond structure vs a one-candle poke." NOT defined by ATR/body-%
  yet and **does NOT reject trades**; recorded so course examples can later be compared to
  live data before deciding if acceptance becomes a formal rule.
- **Displacement quality stays separate** (MSS = "did structure change?"; displacement
  quality = "how convincing?" — different modules; quality threshold still deferred).

### B7 ✅ FROZEN — AMD-phase mechanization `[RES:amd_phase]` (context/ranking only, not a gate)
- **Prior-session ranges are REFERENCE ranges, not automatically Accumulation.** Keep three
  separate objects: `reference_session_range` · `accumulation_candidate` · `confirmed_
  accumulation`. A range earns the `Accumulation` label **only if the price action actually
  shows consolidation/balance** (course L16: accumulation is an observed ranging phase where
  institutional positioning occurs, generally around the opening area — not "whatever session
  came before").
- **Session references:** London candidate → Asia range is the primary prior-session
  reference; NY-AM candidate → London range primary, still-active Asia liquidity remains
  relevant.
- **Consolidation quantitative detector = deferred `[NEC]`** (no ATR/range-width/bar-count/
  compression number frozen now; validate vs course examples later, never from P&L).
- **ORG is NOT accumulation** — it is contextual PD-array / rebalance / S-R / magnet info
  (course L14); may be confluence inside AMD analysis but never defines Accumulation.
- **Directionally agnostic:** do NOT pre-label the day bullish/bearish PO3. Direction emerges
  causally into `bullish/bearish_manipulation_candidate` and `bullish/bearish_distribution_
  candidate`. Daily bias is separate HTF context (B10) — raises confidence, never sets AMD
  direction ahead of price.
- **Causal, provisional phase states** (timestamped in the event trail; promoted only when
  later evidence confirms; **no hindsight relabeling** to justify an earlier setup):
  `ACCUMULATION_CANDIDATE → MANIPULATION_CANDIDATE → MANIPULATION_CONFIRMED →
   DISTRIBUTION_CANDIDATE → DISTRIBUTION_CONFIRMED`. E.g., at first sweep the engine may only
  say MANIPULATION_CANDIDATE; promotion needs the subsequent displacement/MSS/distribution.
- **Role in B3:** contributes to candidate ranking (a sweep that fits Accumulation→
  Manipulation→Distribution outranks an isolated technical sweep) and to the "AMD:"
  recommendation line — **never** a standalone authorize/reject. A trade still requires the
  full structural sequence (liquidity → sweep → displacement → confirmed MSS → same-leg FVG →
  retrace → adequate target).

### B8 ✅ FROZEN — In-FVG entry location `[RES:fvg_entry_loc]`
- **Default (production) = CE / 50% midpoint of the FVG** (consequent encroachment). Modes
  {proximal / CE / distal} remain **configurable** and the selected mode is **recorded on
  each candidate**, but CE is frozen as the production methodology now; **no comparative
  optimization of the three modes at this stage**.
- **Causal fill semantics (frozen):** entry only **strictly after arming**; FVG must be
  **valid/unmitigated at the moment of fill**; if one bar touches the entry level and later
  closes through the far boundary, the **limit fill is treated as first** and the
  close-through as **post-entry management**.
- Never auto-enter at MSS close; location chosen on fidelity grounds, not to pass the 3R gate.

### B9 ✅ FROZEN — Multiple same-leg FVG selection `[RES:fvg_tiebreak]`
Arm **all eligible** same-leg FVGs (correct polarity · same-leg B5 · unmitigated · correct
P/D zone A4). **Entry FVG = the first eligible, still-unmitigated FVG whose CE is reached
strictly after arming (causal "first-touch").** Apply geometry (A8) + the 3R gate (B4) to
**that** FVG only; if it fails → **reject the setup** (NO FVG-shopping for a better R:R;
never move to a deeper FVG to pass 3R). True simultaneous ambiguity (two eligible CEs reached
on the same bar, no deterministic causal ordering) → **NO-TRADE — "ambiguous FVG"**, both
candidates logged. **All eligible FVGs kept in the event trail** for audit.

### B10 ✅ FROZEN — HTF-context labelling `[RES:htf_labeling]`
Four labels: **HTF Aligned** · **Counter-context / Possible Distribution** · **Counter-context
/ Possible Manipulation** · **HTF Neutral**. Inputs causal & structural (§2/B2). **Daily =
primary (dominant) HTF narrative; 4H = secondary modifier** (may read as correction /
transition / emerging reversal → e.g. "Daily: Bearish / 4H: Bullish correction"). Labelling:
aligned if setup dir == daily_bias; if opposed → Possible Distribution when `htf_reversing`
toward the setup dir, else Possible Manipulation; neutral daily → HTF Neutral.
- **`htf_reversing`** = evidence the HTF is **genuinely transitioning** — a *meaningful
  structural shift consistent with the HTF dealing range*, **NOT a single isolated MSS**.
  Exact mechanization = `[NECESSARY_MECHANIZATION]`, deferred.
- **INVARIANT:** HTF context is **never a hard veto**. It contributes to confidence,
  candidate ranking (B3.7), the recommendation explanation, and quality grading (B11) — and
  must never reject an otherwise-valid setup solely because Daily/4H point the other way.

### B11 ✅ FROZEN — Setup Quality A/B/C `[RES:quality_grade]`
- **DIAGNOSTIC-ONLY in Phase 1.** A setup that passes the structural sequence + geometry (A8)
  + minimum-3R (B4) stays ACTIONABLE regardless of grade; quality is reported/logged but
  never suppresses a setup, never converts to NO-TRADE, never alters entry/stop/target.
  (Compare A/B/C to discretionary review + live outcomes before deciding if grade ever gates.)
- **Transparent, rule-based, NO hidden weights / no ML / no P&L optimization**; every
  recommendation shows *why* it got A/B/C.
- **Confluence factors:** (1) swept-ERL significance; (2) HTF context label (B10);
  (3) AMD fit (B7); (4) MSS acceptance state; (5) FVG cleanliness (B9); (6) session-role
  appropriateness; (7) displacement quality (placeholder until its metric lands); (8) R:R
  headroom beyond 3R; **(9) Premium/Discount location quality** (visible in the quality
  explanation even though P/D remains a separate structural gate).
- **Provisional qualitative tiering (boundaries deferred):** A = strong confluence, no
  meaningful soft weaknesses · B = valid setup with limited soft weaknesses · C =
  structurally valid and ≥3R but materially weaker context/confluence. Exact cutoffs frozen
  only after the deferred items (displacement quality, acceptance, `htf_reversing`) are
  mechanized and visually validated vs course examples.

## C. Necessary-mechanization NUMBERS — decided INDIVIDUALLY (user 2026-07-28)
Status: **C1, C2, C4, C9, C10 approved** (C9/C10 relabelled `[COURSE]`); **C3, C5, C6, C7,
C8 NOT approved / unresolved** — no substitute numbers until their dependent structural
decisions (B2/B5) settle. None fitted; sensitivity only after freeze.

**C1 [APPROVED provisional] Session windows (ET)** `[NECESSARY_MECHANIZATION]` — Asia
18:00-00:00; London ACTIVE window 02:00-05:00; NY-AM 08:30-11:00; NY-PM 13:30-16:00. Course
gives Israel-time PREFERRED windows for London/NY, not a canonical ET definition -> this is
an implementation translation. DST must be CALENDAR-AWARE (IL and US switch on different
dates; do NOT assume a permanent 7-hour offset). Distinguish FULL SESSION vs
PREFERRED/ACTIVE window — killzone bounds are not the whole London/NY session.

**C2 [APPROVED] Swing confirmation width (fractal)** `[NECESSARY_MECHANIZATION]` — 1H/15m
candidate pivot width = 2; minor/internal width = 1. Produces CANDIDATE pivots ONLY:
`fractal-confirmed` != `significant swing`. Significance is decided separately under B2
(strong high/low from which a meaningful directional move occurs; HTF stronger; sub-15m less
reliable).

**C3 [NOT APPROVED] "significant swing" >= 1.0 x ATR** — rejected: substitutes a volatility
threshold for a STRUCTURAL concept. Significance defined structurally (B2): confirmed pivot
on 15m+ AND produces a meaningful directional leg AND participates in the active
structure/dealing range AND is not a minor internal fluctuation. Any numeric magnitude stays
EXPLICITLY UNRESOLVED until B2/B5; do NOT freeze 1.0xATR.

**C4 [APPROVED provisional] Equal-H/L tolerance = 0.15 x ATR(TF)** `[NECESSARY_MECHANIZATION]`
— course: equal H/L need the same AREA, not exact price; no number given. Requirements:
record actual inter-member distance + ATR at cluster creation; never P&L-tune; visible in
diagnostics; validate vs course examples before freeze.

**C5 [NOT APPROVED as universal] liquidity-zone width** — pool geometry comes FROM THE
SOURCE, not a symmetric +/-0.15xATR band: equal-H/L -> clustered member prices; multi-swing
-> constituent swing prices; FVG -> its actual high/low; Asia/London -> actual session H/L;
PDH/PDL/PWH/PWL -> the named level. POOL GEOMETRY != SWEEP TOLERANCE. A single-named-level
sweep-penetration tolerance, if required, is a SEPARATE `[NECESSARY_MECHANIZATION]`
(`sweep_tol`), not shared with pool geometry.

**C6 [NOT FROZEN] displacement threshold** — deferred to B5 (displacement-leg definition)
first; then decide the quality metric (ATR / relative candle-body size / multi-bar impulse /
combination). Course: displacement is qualitative ("energetic", "unusually large vs
preceding"), not a number. Never chosen from profitability.

**C7 [NOT APPROVED] 6-tick stop buffer** — the structural stop (beyond manip-extreme /
swept-zone outer boundary) stands; the EXECUTION BUFFER is a SEPARATE
`[NECESSARY_MECHANIZATION]` (`stop_buffer`), INSTRUMENT-AWARE (not one hard 6-tick value
across MES/MNQ/NQ/ES), kept UNRESOLVED for now. Structural boundary never widened to improve
results.

**C8 [REJECTED] fixed bar-count lifetimes (6/8/8)** — use STRUCTURAL EXPIRY: a candidate
dies when (a) its killzone/session ends; (b) the manipulation thesis is structurally
invalidated; (c) a higher-priority manipulation supersedes it (B3); (d) the relevant FVG is
fully mitigated before an actionable retrace; (e) the intended liquidity objective is taken;
(f) a new structural state invalidates it. A hard max-age, only if technically required
later, is a labelled `[NECESSARY_MECHANIZATION]` safety cap — not a course rule.

**C9 [APPROVED] NWOG tracking** `[COURSE]` — keep N=3 NWOGs (course-stated), retain ~3 weeks;
an older un-closed NWOG may stay relevant; NWOG = S/R / price magnet, NOT ERL. The
<=60 / >60 / >200-pt figures remain CONTEXTUAL course statistics only, never hard rules
without independent validation.

**C10 [APPROVED] ORG tracking** `[COURSE]` — track the CURRENT DAY's ORG + its 50% level; NO
stack of historical daily ORGs. The ~70% partial-rebalance figure is metadata/context only,
never a hard signal condition.

**C11 min_rr = 3.0** — frozen in B4.

### §C dependency note
Unresolved items (C3, C5 sweep_tol, C6, C7 stop_buffer, C8 max-age) depend on structural
decisions B2 (range / significant-swing) and B5 (displacement leg). Resolve structure first;
any residual numbers are then decided individually.

## D. Hard rules (not open — enforced regardless)
- Causality / no look-ahead everywhere; prefix-stability test mandatory.
- Never move entry/stop to force geometry; invalid geometry ⇒ reject.
- Never select any value by backtest P&L during implementation.
- NO-TRADE is a valid, first-class output.
- **HTF context is never a hard veto** (B10 invariant): confidence/ranking/explanation/quality
  only — never rejects an otherwise-valid setup on Daily/4H direction alone.
- OOS/hold-out untouched until the spec is frozen.

---
**To proceed:** approve/amend §A (reuse), decide §B (esp. the ⭐ B1–B4), confirm §C numbers.
On approval I freeze these into `ict_live/config.py` with the labels attached, then build in
the sequence in `ARCHITECTURE.md` / the implementation plan — feed + storage + bar-builder
first (with causality tests), then structure/liquidity/PD-arrays, then the candidate/setup
state machine, then recommendations, last the visual-debug renderer.
