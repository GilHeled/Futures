# STATE_MACHINE_SPEC — ict_live setup engine

> The engine tracks **multiple concurrent liquidity/manipulation candidates** and advances
> each through an explicit, auditable state machine. It never "commits to the first sweep."
> Every transition and rejection is written to the event trail. All logic is causal
> (DATA_FEED_SPEC §5). Geometry/FVG/mitigation/arming reuse the course-resolved
> `ict_faithful` logic; the candidate-management + ranking layer is new.

## 0. Two layers
1. **Candidate layer** — maintains a live set of *liquidity/manipulation candidates* and
   ranks them (§10 of the methodology). A candidate is born when a tracked ERL pool is
   swept; it is scored continuously; low-value candidates expire.
2. **Setup layer** — for each live candidate, a per-candidate state machine advances
   sweep → displacement → MSS → FVG → retrace → entry, producing at most one setup.

The recommendation at any tick = the highest-ranked candidate that has reached an
ACTIONABLE setup with valid geometry and R:R ≥ threshold; else NO-TRADE.

## 1. Per-candidate states
```
IDLE
  └─(ERL pool swept: penetration beyond pool zone + rejection back inside)──> SWEPT
SWEPT                      # manipulation in progress
  · track running manipulation extreme (max high / min low across the sequence)
  · track reclaim/rejection of the pool
  └─(energetic displacement leg away from the sweep, causally linked)──────> DISPLACED
  └─(no displacement within max_manip_bars)────────────────────────────────> EXPIRED
DISPLACED                  # displacement leg building (START=manip extreme; END=impulse
  · leg = impulse away from the manip extreme; END = directional extreme before the first   #   exhaustion,
  ·        confirmed width-1 counter-pivot (B5); leg endpoint known only when that pivot     #   B5)
  ·        confirms (causal; provisional until then)
  · MSS structural swing = most recent confirmed STRUCTURAL opposing swing of the candidate's
  ·        signal TF, formed BEFORE the manipulation began (B6); store TF/degree/ts/why
  └─(price interacting w/ structural swing, no break)──────────────────────> MSS_POTENTIAL
MSS_POTENTIAL
  └─(body CLOSE beyond the structural swing)────────────────────────────────> MSS_CANDIDATE
MSS_CANDIDATE              # break registered, not yet validated as same-leg/causal
  · set mss_acceptance_state ∈ {pending, accepted, weak_acceptance}  (DIAGNOSTIC ONLY)
  └─(shown to belong to the same displacement leg from the manip extreme, B5 causality)─> MSS_CONFIRMED
  └─(structure fails / retraces through pre-MSS structure)──────────────────> EXPIRED
MSS_CONFIRMED              # only a CONFIRMED MSS advances; scan the COMPLETE displacement leg for same-leg FVGs
  · enumerate all 3-candle FVGs of the correct polarity within the leg (not MSS±1)
  · keep all; mark each unmitigated/mitigated (body-close far-boundary, interp A)
  · eligibility filter: FVG in the correct Premium/Discount zone of the dealing range
  └─(≥1 eligible unmitigated same-leg FVG exists)──────────────────────────> ARMED
  └─(no eligible/unmitigated FVG)──────────────────────────────────────────> REJECTED(no_fvg)
ARMED                      # armed at FVG formation (k+1), NOT at MSS close
  · a limit rests at the configured in-FVG location (touch / CE-midpoint / far edge)
  · entry may fill only on a bar STRICTLY AFTER arming (no pre-confirmation interaction)
  └─(price retraces into FVG @ entry location, still unmitigated)───────────> ENTRY_READY
  └─(FVG mitigated before retrace  |  entry_window elapsed  |  killzone/session end)──> EXPIRED
ENTRY_READY                # validate geometry + R:R
  · geometry: SHORT entry < manip_extreme & stop > manip_extreme (mirror LONG); else REJECT
  · stop = beyond max(manip_extreme, swept-zone outer boundary) + buffer
  · target = next valid liquidity objective; R:R from entry+stop
  └─(geometry valid AND R:R ≥ min_rr[=3])──────────────────────────────────> ACTIONABLE
  └─(invalid geometry OR R:R < min)────────────────────────────────────────> REJECTED(geometry|rr)
```
Terminal: `ACTIONABLE`, `REJECTED(reason)`, `EXPIRED(reason)`. Every terminal + every
transition is logged with timestamp, prices, and the deciding values.

## 2. Candidate ranking — FROZEN B3: explicit LEXICOGRAPHIC, not a learned score
Maintain **multiple active candidates**; a **later, stronger candidate may SUPERSEDE an
earlier weak sweep**. Ranking is an **explicit lexicographic / rule-based priority with NO
hidden weights** (Phase 1). Evidence is exposed per candidate; compare candidates in this
order: (1) liquidity significance [HTF ERL > minor/internal; named PDH/PDL, PWH/PWL,
Asia/London H/L, clear equal H/L, significant 15m+ swings]; (2) location [BSL-sweep-in-
Premium for short / SSL-sweep-in-Discount for long]; (3) AMD context [plausibly manipulation
vs ordinary continuation]; (4) displacement quality; (5) causal MSS [same displacement
produced the shift]; (6) same-leg FVG still actionable; (7) HTF alignment [raises confidence,
**not** a veto]; (8) target availability. **Every rejected/superseded candidate is stored
with its reason.** Genuine ambiguity ⇒ **NO-TRADE ("ambiguous manipulation")**, never a
coin-flip. (Phase-1 rule-based only; a learned/weighted score is explicitly out of scope.)

## 2b. AMD phase track (B7 — context, parallel; NOT a gate)
A parallel, **directionally-agnostic** context track with causal, timestamped states —
`ACCUMULATION_CANDIDATE → MANIPULATION_CANDIDATE → MANIPULATION_CONFIRMED →
DISTRIBUTION_CANDIDATE → DISTRIBUTION_CONFIRMED` (+ bullish/bearish sub-label that emerges
only as price develops). Keeps `reference_session_range` / `accumulation_candidate` /
`confirmed_accumulation` as **separate** objects; a range is `Accumulation` only on observed
consolidation (numeric detector deferred `[NEC]`). Session refs: Asia→London, London→NY
(Asia still relevant). Promotion requires later evidence; **no hindsight relabeling** may
create a retroactive setup. Feeds candidate ranking (§2 criterion 3) and the "AMD:"
recommendation line **only** — never authorizes/rejects a trade on its own.

## 3. Dealing range & Premium/Discount coupling — FROZEN B2
The active dealing range = the **most recent meaningful completed structural swing leg on the
analyzed TF** (bullish: significant confirmed Swing Low→High; bearish: High→Low). Premium =
upper half, Discount = lower half, EQ = 50%. **Nested ranges are preserved hierarchically
W→D→4H→1H→15m**, each tagged with its **source timeframe** (HTF ranges are context for LTF
ranges). A new range replaces the old **only after a new meaningful structural leg
confirms**. When the range changes, each live candidate's P/D location + FVG eligibility are
re-evaluated and logged. The engine **reports which range a setup uses and why**. A setup is
never kept alive by silently reselecting a range that "makes it pass."

## 4. Causality within the machine
- MSS confirmation requires the confirming close to be a **closed** bar; the opposing swing
  must itself be confirmed (its confirmation-width future bars closed).
- FVG requires its 3rd candle closed; a forming higher-TF bar cannot arm a setup.
- ENTRY_READY requires the retrace bar to close at/through the entry location **after**
  arming; a pre-arming touch never counts (§13). If no post-MSS retrace occurs before
  expiry → EXPIRED("no_post_confirmation_retrace"), a first-class NO-TRADE.

## 5. Lifetimes & expiry `[NEC]`
- `max_manip_bars` (SWEPT→DISPLACED window), `max_leg_bars` (DISPLACED→MSS), `entry_window`
  (ARMED→ENTRY_READY), and `max_setup_lifetime` overall, plus killzone/session-end flatten.
  All are declared mechanization values (`UNRESOLVED_DECISIONS.md`), not invented inline.

## 6. Event trail (auditability)
Each candidate carries a stable id; every state change appends a line, e.g.:
```
09:31 [C7] Asia-High pool active  zone=25238.00–25242.00
09:36 [C7] BSL penetration  high=25246.25
09:39 [C7] manip_extreme -> 25247.50
09:42 [C7] bearish displacement candidate  body=..  atr=..
09:47 [C7] MSS confirmed  close=25220.00 < swing 25231.50
09:47 [C7] FVG#173 registered  25218.25–25225.50  (same-leg, unmitigated, premium)
09:52 [C7] retrace into FVG @CE 25222.00
09:52 [C7] target PDL 25138.00  R:R=3.11  -> ACTIONABLE SHORT (rank #1 of 2)
09:52 [C3] REJECTED(rr)  nearest BSL target only 1.8R
```
This trail is what we diff against discretionary human reads in the fidelity audit; it is
the same substrate `ict_faithful`'s blind rounds used.

## 7. What is reused vs new
- **Reused (course-resolved):** sweep/manip-extreme tracking, displacement→FVG attribution,
  body-close mitigation, P/D eligibility, arm-at-k+1, geometry invariants, stop rule.
- **New:** the multi-candidate set + ranking (§2), the deterministic dealing-range selector
  (§3), full multi-bar displacement-leg scan (`[RES:disp_leg]`), HTF-context labelling,
  liquidity-target R:R (§16), the event trail, and NWOG/ORG PD-arrays as context/targets.
