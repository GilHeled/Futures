# FROZEN_DECISIONS — ict_live (consolidated sign-off, 2026-07-28)

All of B1–B11 and §C are resolved. This is the authoritative summary the engine is built
against. **Do not reopen a frozen item unless a genuine contradiction surfaces during
coding** (then document it, don't silently change). Detail + rationale live in
`UNRESOLVED_DECISIONS.md`; methodology mapping in `METHODOLOGY_SPEC.md`.

## 1. FULLY FROZEN methodology rules (structural — course-grounded)
- **A1–A9 (carried from `ict_faithful`):** FVG geometry (bull high₁<low₃ / bear low₁>high₃);
  detector purely geometric (displacement is a setup filter, not part of the FVG def); FVG
  mitigation = body close through the FAR boundary; FVG multi-eligibility = correct P/D zone;
  displacement→FVG boundary = the impulse that creates the MSS (FVG may complete at k+1);
  arm at FVG formation (k+1), not at MSS close; manipulation-extreme = running max/min, kept
  separate from pool price & sweep bar; geometry invariants (entry below/above manip-extreme,
  stop beyond it, invalid ⇒ reject, never adjust); Premium/Discount = Fib-50% of the range.
- **B1 Timeframe roles:** W/D/4H = HTF context+major liquidity · 1H = primary structural TF ·
  15m = valid intraday TF & lowest TF for significant swings/liquidity · 5m = optional
  refinement only (never required if valid on the active TF, never to inflate count) · signal
  TF recorded on every candidate/recommendation · configurable, never P&L-chosen.
- **B2 Dealing-range selector:** most recent meaningful completed structural swing leg on the
  analyzed TF (bull Low→High / bear High→Low); nested ranges preserved hierarchically
  W→D→4H→1H→15m with source-TF tagged; replaced only on a new confirmed meaningful leg;
  engine reports which range + why. (Numeric "significant" = deferred, §3.)
- **B3 Manipulation ranking:** multiple live candidates; explicit LEXICOGRAPHIC priority
  (significance → location P/D → AMD → displacement → causal MSS → same-leg FVG → HTF (non-
  veto) → target); a later stronger candidate SUPERSEDES an earlier weak sweep; every
  rejected/superseded candidate + reason stored; ambiguity ⇒ NO-TRADE. No learned score, no
  hidden weights (Phase 1).
- **B4 Targets / R:R:** `min_rr = 3.0` to the actual next meaningful liquidity objective
  (ERL/PDH-PDL/PWH-PWL/active Asia-London H/L/equal H/L/15m+ swing/HTF); <3R ⇒ reject; >3R ⇒
  keep target (no truncation); IRL (FVG/NWOG/ORG) = intermediate only; `fixed_3R` = research
  mode only.
- **B5 Displacement leg:** START = true manipulation extreme; END = directional impulse
  extreme before the first confirmed width-1 counter-pivot (impulse exhaustion, causal — leg
  endpoint known only on pivot confirmation; provisional until then); same-leg FVG =
  correct-polarity, forms after manipulation begins, belongs to the MSS-causing displacement,
  middle candle inside the finalized leg, still unmitigated when eligible; 3-candle def not
  loosened. Multi-bar. Prefix-stability/no-look-ahead test mandatory.
- **B6 MSS:** must break a **structural** opposing swing (analysis-TF, part of current
  structure, formed before manipulation begins — not width-1 internal, not remote fractal),
  by **body close**; states **Potential → Candidate → Confirmed** (only Confirmed advances);
  evaluated on the candidate's signal TF; displacement quality kept separate.
- **B7 AMD:** prior-session ranges are REFERENCE ranges, Accumulation only on observed
  consolidation (`reference_session_range`/`accumulation_candidate`/`confirmed_accumulation`
  kept separate); Asia→London, London→NY references; ORG is NOT accumulation; directionally
  agnostic (direction emerges causally); states ACCUMULATION_CANDIDATE → MANIPULATION_
  CANDIDATE → MANIPULATION_CONFIRMED → DISTRIBUTION_CANDIDATE → DISTRIBUTION_CONFIRMED, no
  hindsight relabeling; feeds B3 ranking + "AMD:" line only, never a gate.
- **B8 Entry:** default = CE / 50% of the FVG; causal fill (strictly after arming; FVG
  unmitigated at fill; same-bar touch-then-close-through ⇒ fill first, close-through =
  post-entry). Modes {proximal/CE/distal} configurable + recorded; CE = production.
- **B9 FVG selection:** arm all eligible same-leg FVGs; entry = first eligible unmitigated CE
  reached strictly after arming (causal first-touch); geometry+3R applied to THAT FVG, fail ⇒
  reject (no FVG-shopping); simultaneous ambiguity ⇒ NO-TRADE "ambiguous FVG"; all eligible
  logged.
- **B10 HTF labels:** {HTF Aligned · Counter-context/Possible Distribution · Counter-context/
  Possible Manipulation · HTF Neutral}; Daily = primary, 4H = secondary modifier. **INVARIANT:
  HTF is never a hard veto** — confidence/ranking/explanation/quality only.
- **B11 Quality A/B/C:** diagnostic-only in Phase 1 (never gates/alters the trade);
  transparent rule-based, no hidden weights; factors incl. Premium/Discount-location quality;
  provisional qualitative A/B/C (boundaries deferred).
- **Hard invariants (§D):** causality / no look-ahead everywhere + prefix-stability test;
  never move entry/stop to force geometry (invalid ⇒ reject); never P&L-select during
  implementation; NO-TRADE is first-class; HTF never a veto; OOS/hold-out untouched until the
  spec is frozen.

## 2. PROVISIONAL mechanizations (frozen values, revisit only via validation vs course)
- **C1** Session windows (ET): Asia 18:00–00:00 · London active 02:00–05:00 · NY-AM
  08:30–11:00 · NY-PM 13:30–16:00 · `[NEC]`; DST calendar-aware (no fixed 7h offset);
  full-session ≠ active-window.
- **C2** Fractal confirmation width: 1H/15m = 2, minor = 1 · `[NEC]` · CANDIDATE pivots only
  (fractal-confirmed ≠ significant swing).
- **C4** Equal-H/L tolerance = 0.15×ATR(TF) · `[NEC]` · record actual distance + ATR; validate
  vs course examples before final freeze.
- **C9** NWOG: N=3, ~3 weeks, older-if-unclosed, = S/R/magnet not ERL · `[COURSE]`; ≤60/>60/
  >200-pt figures = context stats only.
- **C10** ORG: current-day gap + 50%, no history · `[COURSE]`; ~70% rebalance = context only.
- **B8** entry mode = CE (production); **B4** min_rr = 3.0.

## 3. STILL-DEFERRED numeric/mechanical choices (NOT frozen; needed before final freeze)
Resolve after the structural engine exists + can be visually validated vs course examples;
never P&L-selected:
- **Significant-swing magnitude** (B2/C3) — currently structural only; any numeric threshold
  unresolved.
- **Consolidation detector** for Accumulation (B7) — no ATR/width/bar-count/compression number.
- **Displacement quality metric** (B5/C6) — ATR vs relative-body vs multi-bar impulse vs
  combination; leg extent is frozen, the *quality* threshold is not.
- **`sweep_tol`** (C5) — single-named-level penetration tolerance (separate from pool geometry,
  which comes from the source).
- **`stop_buffer`** (C7) — instrument-aware execution buffer (not one 6-tick value).
- **`htf_reversing`** mechanization (B10) — "genuine HTF transition, not one isolated MSS."
- **`max_setup_age`** (C8) — optional housekeeping cap; primary expiry is structural (B/§C).
- **Quality A/B/C exact boundaries** (B11) — provisional qualitative tiers until the above land.
- **`mss_acceptance_state`** thresholds (B6) — diagnostic-only for now.

## 4. Build order (unchanged)
config.py (freeze §1–2 with labels) → feed + storage + bar-builder + calendar (**causality /
prefix-stability tests first**) → structure/liquidity/PD-arrays → candidate + setup state
machine + ranking → risk (stop/target/RR) → recommendations (model/scorer/formatter + event
trail) → FastAPI (/webhook, /status, /health) → visual-debug renderer. Deferred items get
placeholders that raise `NotImplementedError`/log `unresolved` rather than a silent number.
Backtest/validation only after the spec is frozen; OOS untouched.
