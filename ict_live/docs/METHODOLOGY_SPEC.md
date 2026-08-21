# METHODOLOGY_SPEC — ict_live (authoritative course methodology → implementation)

> The course methodology (user's 20 sections) is AUTHORITATIVE. This maps each section to
> its implementation, marks what is already **course-resolved** (mostly lifted from
> `ict_faithful/SPEC.md`) vs what is an **open decision** (see `UNRESOLVED_DECISIONS.md`).
> Labels: `[COURSE]` = fixed by the course; `[NEC]` = necessary mechanization (a number the
> course doesn't give but the code needs); `[RES]` = research choice (genuinely open, needs
> approval, never P&L-selected).

## 1. Multi-timeframe context — FROZEN B1
Roles: **W/D/4H** = HTF context + major liquidity; **1H** = primary structural/liquidity
analysis TF; **15m** = valid intraday structure/liquidity TF and the **lowest TF on which
significant swing/liquidity points are normally defined**; **5m** = optional intraday
reversal/entry refinement *where appropriate* (never required if the setup is already valid
on the active TF; never used to inflate trade count). **Every candidate & recommendation
records its explicit signal timeframe.** Configurable across faithful {1H / 15m / +5m}
behaviors; never chosen by P&L. HTF is **context, not a hard veto** `[COURSE]`.

## 2. Market structure `[COURSE]`
Uptrend = ≥2 higher highs AND ≥2 higher lows; downtrend = ≥2 lower lows AND ≥2 lower highs.
Distinguish **potential reversal** (a failed HH/LL, or arrival at a strong level) from
**confirmed reversal** (new HL+LH / LH+HL sequence actually forms). Swing confirmation width
`[NEC:swing_width]`. Reuse `mnq_system.swings` / `ict_faithful` fractal+confirm.

## 3. Liquidity `[COURSE]` (+ `[NEC]` zone widths)
Pools: PDH/PDL, PWH/PWL, Asia H/L, London H/L, equal highs, equal lows, significant swing
H/L. Represented as **zones/pools, not single prices** `[NEC:pool_width]`. Equal H/L need
not be pixel-perfect → clustering tolerance `[NEC:equal_tol]`. HTF liquidity is more
significant (feeds candidate ranking §10). "Significant swing" definition `[RES:sig_swing]`
(not every minor local swing). Reuse `ict_faithful` named-level + clustering machinery.

## 4. ERL vs IRL `[COURSE]`
ERL = liquidity **outside** the active dealing range (significant highs/lows, equal H/L).
IRL = **inside** the range (FVG, internal S/R, imbalance, gaps). Movement is *conceptually*
ERL→IRL→ERL but **not a deterministic rule** `[COURSE]`. Classification depends on the
active dealing range (§5), so ERL/IRL is recomputed whenever the range updates.

## 5. Premium / Discount `[COURSE]`
Active dealing range + 50% equilibrium; upper half = Premium, lower half = Discount. Shorts
sought in Premium, longs in Discount; never chase. **Dealing-range selection is FROZEN — see B2**:
most recent *meaningful completed structural swing leg* on the analyzed TF (bullish Low→High
/ bearish High→Low), nested ranges preserved hierarchically W→D→4H→1H→15m with source-TF
tagged, replaced only on a new confirmed meaningful leg; engine reports which range + why.
Deterministic, auditable, never chosen to make a setup pass.

## 6. Fibonacci / dealing range `[COURSE]`
Levels 0 / 0.5 / 0.62 / 0.79 / 1; **50% is primary**; a technical retrace generally reaches
~≥50% of the measured move. Swing selection for the measured move must be **meaningful
structural moves** `[RES:sig_swing]`, not every local high/low.

## 7. FVG — **mostly RESOLVED in `ict_faithful/SPEC.md §7b`**
- 3-candle imbalance: bullish high₁<low₃ ; bearish low₁>high₃ `[COURSE]`.
- Detector is **purely geometric**; "energetic/displacement" is a setup-stage filter, not
  part of the FVG definition `[COURSE]`.
- FVG is an **area**; higher-TF FVGs more significant `[COURSE]`.
- **Mitigation = body close through the FAR boundary (interp A)**; tracked explicitly; an
  already-mitigated FVG is never a future entry `[COURSE-grounded, interp A was RES→resolved]`.
- FVG is **not** itself an entry trigger `[COURSE]`.
- **Same-displacement attribution:** scan the COMPLETE displacement leg (not MSS±1 bar);
  keep all valid same-leg FVGs initially `[COURSE]`.
- **Selection when several exist:** eligibility = correct P/D zone `[COURSE]`; same-zone
  tie-break is a **documented gap** `[RES:fvg_tiebreak]` (do NOT auto-pick "deepest").

## 8. Displacement
Energetic/momentum move **causally linked to the liquidity event + structural shift** — not
"big candle = true" `[COURSE]`. Any ATR/'% body' threshold is an explicit mechanization
`[NEC:disp_threshold]`. **Single-bar vs multi-bar displacement:** `ict_faithful` used
single-bar; the new spec (§7/§13) explicitly wants the **full multi-bar displacement leg**
scanned → implement leg-based displacement `[RES:disp_leg]` (this reopens, per the new spec,
the item `ict_faithful` had deferred).

## 9. MSS
Not merely "crossed a swing". Sequence-in-context: liquidity event → displacement →
meaningful structural break/close → new flow. Distinguish **candidate MSS** vs **confirmed
MSS** `[COURSE]`. "Meaningful break" = close beyond the last opposing confirmed swing of the
relevant degree `[NEC:mss_rule]` (degree/what counts as the opposing swing is `[RES]`).

## 10. Power of 3 / AMD — **the hard part**
Accumulation → Manipulation → Distribution; manipulation is counter to distribution `[COURSE]`.
**Do NOT trade the first valid sweep.** FROZEN B3: maintain **multiple candidate
liquidity/manipulation events**, ranked by an **explicit lexicographic priority (no learned
score, no hidden weights)** — (1) liquidity significance, (2) location P/D, (3) AMD context,
(4) displacement quality, (5) causal MSS, (6) same-leg FVG actionable, (7) HTF alignment (not
a veto), (8) target availability. A later, stronger candidate may **supersede** an earlier
weak sweep; every rejected/superseded candidate + reason is stored; ambiguity ⇒ NO-TRADE.
AMD-phase mechanization still open `[RES:amd_phase]` (B7).

## 11. Sessions `[COURSE mapping]` + `[NEC windows]`
Track Asia, London, NY-AM, NY-PM. Significance: Asia range creation; London interaction with
Asia liquidity; NY continuation/reversal of London. Windows **configurable, ET-internal,
DST-correct**; **no hard-coded Israeli clock** `[COURSE]`. Proposed ET windows (confirm):
Asia 18:00–00:00, London 02:00–05:00, NY-AM 08:30–11:00 (or 09:30–11:00), NY-PM 13:30–16:00
`[NEC:session_windows]`.

## 12. Sweep — **geometry RESOLVED in `ict_faithful`**
Track: pool type, pool boundaries, first-penetration time, running manipulation extreme,
reclaim/rejection, subsequent displacement, subsequent MSS. **Keep pool price, sweep bar,
and manipulation extreme SEPARATE** `[COURSE]`. SHORT: BSL swept → manip extreme = highest
high of the manipulation sequence → entry below it, stop above it. LONG mirror. Manipulation
extreme = running max/min updated across the sequence (already in `ict_faithful`).

## 13. Preferred setup sequence `[COURSE]`
ERL approached → sweep/manipulation → displacement → confirmed MSS → valid **same-leg** FVG
→ **later** retrace into that FVG → entry. Entry must be **causally after** MSS confirmation
(no pre-confirmation FVG interaction reused as a later fill). If no post-MSS retrace → NO
TRADE. (Arming at FVG formation k+1, fill on a strictly later retrace — `ict_faithful §7c`.)

## 14. Entry
Preferred = retrace into the same-leg FVG; **not** auto-enter at MSS close `[COURSE]`. Exact
in-FVG location **configurable + documented, never P&L-selected** `[RES:fvg_entry_loc]`
(candidates: full touch / midpoint (CE) / far edge). `ict_faithful` used CE midpoint.

## 15. Stop `[COURSE]` + `[NEC buffer]`
Beyond structural invalidation = beyond max(true manipulation extreme, swept-zone outer
boundary) + fixed buffer `[NEC:stop_buffer]`. **Never** a tiny stop for R:R; **never** move
entry/stop to force geometry — invalid geometry **rejects** the setup (enforced already in
`ict_faithful`).

## 16. Targets & R:R — FROZEN B4
Distinguish **fixed-3R** from **minimum-1:3 liquidity opportunity** (authoritative = the
latter). Next meaningful liquidity objective → R:R from proposed entry + structural stop →
**require ≥1:3 (`min_rr=3.0`)**; <3R ⇒ **reject**; >3R ⇒ keep the liquidity target (don't
truncate to 3R). Target hierarchy (frozen): opposing significant ERL · PDH/PDL · PWH/PWL ·
active Asia/London H/L · equal H/L · significant 15m+ swing · relevant HTF objectives. **IRL
(FVG/NWOG/ORG) = intermediate reaction areas, not an automatic replacement** for the primary
external target unless context makes it the intended destination. `fixed_3R` = research mode
only.

## 17. Daily bias & 4H context — labels, not vetoes `[COURSE]`
Daily bias + 4H recorded as context/confidence, **not** an automatic counter-direction veto.
The engine labels each setup: **HTF-aligned / counter-context / possible-manipulation /
possible-distribution** `[RES:htf_labeling]` (how the label is computed).

## 18. NWOG `[COURSE]` + `[NEC]`
New Week Opening Gap: S/R, rebalance area, magnet/target. **Not** classified as ERL. Track
recent NWOGs `[NEC:nwog_tracking]`. Course observation: small gaps (≈≤60 pts in examples)
tend to at least partially rebalance — **course-specific observation, not a market law**
`[COURSE-observation]`.

## 19. ORG `[COURSE]` + `[NEC]`
Opening Range Gap: rebalance / S/R / contextual target / opening-day potential. Track ≥ its
50% level `[NEC:org_tracking]`. Course observation: ~70% partial rebalance in examples —
course material, not verified universal `[COURSE-observation]`.

## 20. NO-TRADE is first-class `[COURSE]`
Valid reasons: wrong P/D location; liquidity not significant; no displacement; no confirmed
MSS; no valid FVG; FVG already mitigated; no post-confirmation retrace; poor R:R to next
liquidity; setup already extended `[NEC:setup_lifetime]`; ambiguous manipulation; conflicting
structure. Do not force trade frequency.

## Recommendation output
Per `recommendations/formatter.py`, matching the user's example format (Symbol, Session,
Status, Quality, HTF context, AMD, Liquidity+pool+sweep+manip-extreme, Location, Displacement,
MSS, FVG+status, Entry, Stop, Target, Risk, Reward, R:R, Reason). Quality grade A/B/C via an
explicit scorer `[RES:quality_grade]`. NO-TRADE prints the candidate + the ordered reasons it
failed (from the event trail).

## Validation (AFTER the spec is frozen — not during implementation)
Dev backtest → matched-random benchmark → cost sensitivity → walk-forward → bootstrap →
**locked OOS touched once**. The OOS/hold-out stays untouched until the spec is frozen. No
P&L-driven tuning before freeze. (Same discipline as every prior line here.)
