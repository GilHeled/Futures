# ICT Course → v2 Code & Dashboard Coverage Map

**Purpose.** v2's objective is to faithfully mechanize **every rule this course teaches** — and to let
you point at any mechanical rule and see exactly where it lives in code and on the dashboard. This is
the living map. Each rule is classified and anchored to its implementation across the three layers.

## Authoritative source

The course rules are taken from the in-repo **authoritative distillation** (the raw `Trade/` lesson
transcripts are *not* in this repo; the specs below cite them as lessons 3–16):

- **`ict_live/docs/METHODOLOGY_SPEC.md`** — the 20-section methodology, tagged `[COURSE]` / `[NEC]`
  (necessary mechanization) / `[RES]` (open research choice). The master enumeration.
- **`mnq_system/strategies/ict_faithful/SPEC.md`** — resolved candle-level detail (FVG geometry &
  mitigation, arming k+1, displacement-leg boundary, P/D eligibility). Cites lessons 5,6,9,12,15,16.
- **`mnq_system/strategies/ict_amd/SPEC.md`** — Power-of-3/AMD, sessions, HTF context; per-concept
  lesson tags (5=killzones, 8/15=bias, 9=P/D, 12=FVG, 15=MSS).

> If the raw `Trade/` lessons are added to the repo later, this map should be re-verified at transcript
> granularity. Until then the 20-section methodology is the stated authority and is treated as such.

## Layers & legend

- **v1** = `ict_live/structure/*` + `ict_live/engine/pipeline.py` (frozen; reused read-only by v2).
- **v2** = `ict_v2/*` (the multi-timeframe cascade + gating + candidate model).
- **DB** = the dashboard **V2 tab**, `ict_live/replay/dashboard.py` (`v2Tables` ~L569, candidate
  drill-down `_candCard` ~L627, `openCandidates` ~L668).

Status:

- ✅ **Implemented** — mechanized and faithful to the course rule.
- 🟡 **Partial** — present but incomplete, or computed in v1 but not surfaced/used in v2/DB.
- ❌ **Missing** — not mechanized anywhere in the three layers (often a `config` sentinel only).
- ℹ️ **Informational / intentionally non-mechanical** — the course frames it as context/observation,
  or we deliberately left it as a documented gap (never silently invented).

---

## Coverage by methodology section

### 1. Multi-timeframe context — `[COURSE]` (METHODOLOGY §1) — ✅ Implemented
The W/D/4H→1H→15m→1m role split *is* v2's spine.
- v1: TF roles/widths in `config.TF_ROLES`, `FRACTAL_WIDTH`; pipeline accepts `structural_by_tf`/`refine_bars` (`pipeline.py:155`).
- v2: `analyze_mtf` (`pipeline.py:520`), `MTFEngine` (`engine.py:20`), live resampling (`live.py:56`); optional `refine_tf`=5m, `anchor_tf`=D/W.
- DB: four stage rows per symbol (`dashboard.py:606`); anchor/refine in banner (`:577`).
- ℹ️ Nuance: no literal per-candidate "signal TF" field; each stage's TF is implicit in its layer. **Minor gap** if you want it stamped on every candidate.

### 2. Market structure — `[COURSE]` (§2) — 🟡 Partial (v1 complete; v2/DB surface only bias)
- v1 ✅: fractal swings + confirm-width (`swings.py:31`), HH/HL/LH/LL skeleton + dominant/broken/protected (`significance.py:46`). Potential vs confirmed reversal is carried by MSS states (§9).
- v2 🟡: derives only a directional **bias** from the last structural leg (`pipeline.py:259 _bias_from_range`, `htf_bias_of:263`); does not re-expose the HH/HL structure.
- DB 🟡: shows bias per stage; **raw swing structure / reversal state not visualized**.
- **Gap:** surface the structural read (HH/HL sequence, potential-vs-confirmed reversal) in v2/DB.

### 3. Liquidity pools / BSL/SSL — `[COURSE]`+`[NEC]` (§3) — 🟡 Partial
- v1 ✅(core): period/session pools `liquidity.py:42` (PDH/PDL, PWH/PWL, Asia/London/NY-AM/NY-PM), swing BSL/SSL `swing_liquidity.py:34`.
- v1 🟡: pools carry a **single `price`, not a zone/band** (§3 wants zones ± tol); **equal-highs/lows clustering NOT implemented** (deliberately excluded, `liquidity.py:9`; `EQUAL_HL_TOL_ATR` is a provisional number only).
- v2: consumes v1 `active_erl` as "the draw" (`align.py:13`). DB: shows the single draw price, not the full pool set.
- **Gaps:** (a) pools-as-zones, (b) equal-H/L clustering, (c) surface the full pool list.

### 4. ERL vs IRL — `[COURSE]` (§4) — 🟡 Partial (ERL only)
- v1: every pool flagged `erl=True` (`liquidity.py:37`); **IRL (FVG/NWOG/ORG internal) classification not implemented** (`liquidity.py:12`). v2/DB: no ERL/IRL label.
- **Gap:** an IRL classifier + ERL/IRL labelling relative to the active dealing range.

### 5. Premium / Discount — `[COURSE]` (§5, lesson 9) — ✅ Implemented
- v1: `dealing_range.py:40 zone_of` (premium/discount/EQ, CE `:59`).
- v2: gate requires longs in discount / shorts in premium (`align.py:41`); entry P/D tagged (`pipeline.py:392`).
- DB: `P/D` fact in the candidate card (`dashboard.py:646`).

### 6. Fibonacci / dealing range — `[COURSE]` (§6) — 🟡 Partial
- v1 ✅: dealing range = most-recent completed opposing structural leg (`dealing_range.py:48 range_for_tf`); nested W→D→4H→1H→15m set (`:72 dealing_ranges`).
- v1 ❌: **only the 50% (CE) level exists** — the fib ladder **0 / 0.62 / 0.79 / 1 is not implemented**.
- v2 🟡: uses `ms.ranges[0]` only (first TF's range) — **nested ranges not surfaced** (`pipeline.py:278`). DB: shows `low–high CE`, no fib ladder.
- **Gaps:** (a) fib ladder levels, (b) surface the nested range hierarchy.

### 7. FVG lifecycle & TF rules — `[COURSE]` (§7, lesson 12) — ✅ Implemented (one documented gap)
- v1: `fvg.py:44 detect_fvgs` — geometry (`:57`), CE (`:67`), **body-close-through-far-boundary mitigation** (`:75/:81`), full same-displacement-leg scan (`:53`), depends_on displacement+MSS. MTF refine variant `detect_fvgs_mtf:107`. P/D eligibility applied at the gate.
- v2: FVG is the sole execution model (`entry_models.py fvg_entries`; ref=CE, invalidation=far edge, prefers unmitigated). DB: entry-model + lifecycle pill (`dashboard.py:655`), invalidation fact (`:647`).
- ℹ️ **Same-P/D-zone tie-break** (>1 eligible FVG) is an **intentionally unresolved documented gap** (`ict_faithful/SPEC.md §7b`) — not silently invented.

### 8. Displacement — `[COURSE]`+`[NEC]` (§8) — ✅ Implemented
- v1: leg-based `displacement.py:39` (manipulation-extreme → first counter-pivot; net/span/exhausted); quality **ranked, not gated** (`pipeline.py:82`).
- v2: best-ranked displacement per sweep (`pipeline.py:334`). DB: "displacement" node in the candidate chain.
- ℹ️ Single-bar vs multi-bar setup-displacement was **resolved to single-bar** on evidence (`ict_faithful §7c`); multi-bar is a documented, un-adopted possibility.

### 9. MSS — `[COURSE]`+`[NEC]` (§9, lessons 15/16) — ✅ Implemented
- v1: `mss.py:40 detect_mss` — opposing pre-manip swing target, **potential→candidate→confirmed** via body close, acceptance distance; ranked (`pipeline.py:101`).
- v2: attached per displacement (`pipeline.py:334`); `mss_state` in the candidate dict. DB: MSS chain step + note.

### 10. Power of 3 / AMD — `[COURSE]` (§10, lessons 3–16) — 🟡 Partial
- ✅ Mechanized behaviors: **multiple ranked candidate sweeps** (`pipeline.py:183`), **explicit lexicographic ranking** (`ranking.py:45`), **supersession + session expiry** (`lifecycle.py:32`), "don't trade the first sweep" (emergent from ranking/lifecycle).
- ❌ Missing: an **explicit AMD-phase model** (accumulation / manipulation / distribution labels) and the **accumulation/consolidation detector** (`config.CONSOLIDATION_DETECTOR` = DEFERRED, `amd_phase` `[RES]`).
- v2: ranked sweeps are the candidate anchors. DB: candidate list + per-item reasons.
- **Gap:** name & expose the AMD phase per candidate; consolidation/accumulation detector.

### 11. Sessions / killzones — `[COURSE map]`+`[NEC]` (§11, lesson 5) — ✅ Implemented (as context)
- v1 ✅: `market/sessions.py` (`active_windows`, `in_session`, `killzone`, DST-safe) + `config.SESSIONS` (Asia/London/NY-AM/NY-PM, ET).
- v2 ✅: `pipeline.session_of(dt)` (`pipeline.py`, reuses v1, naive→UTC normalized) → `(session, killzone)`; every `Candidate` tagged `.session`/`.killzone` (of the manipulation) in both `_partial` and full paths; `to_dict` serializes them; live `snapshot()` adds the **current** `session`/`killzone` (`live.py`).
- DB ✅: current-session chip in each V2 ticket header (`.v2sess`, highlighted when a trading killzone `.kz`); per-candidate "session" fact in the drill-down card (`_candCard`).
- ℹ️ **Context, not a gate** — faithful to §11 (significance/timing) and §1 (HTF is context, not a veto). The stricter "entries only inside killzones" rule (faithful §5, lesson 5) is deferred to the **course-filter layer** (see §16 resolution), where it will be an optional named filter, not structural invalidation.
- Verified: `test_session_and_killzone_context` (DST-correct summer/winter; candidate tagging), `test_snapshot_is_persistable` (snapshot keys).

### 12. Sweep / manipulation — `[COURSE]` (§12) — ✅ Implemented
- v1: `manipulation.py:41 detect_sweeps` — wick-through + close-back rejection; keeps **pool_price / bar_index / extreme separate** (`Sweep:27`); first-penetration via swing_liquidity; ranked by rejection strength (`pipeline.py:48`).
- v2: the sweep is the candidate anchor (`pipeline.py:329`). DB: sweep pool/extreme in the candidate dict + "sweep" node.
- ℹ️ Multi-bar reclaim explicitly left open (`manipulation.py:12`).

### 13. Preferred setup sequence — `[COURSE]` (§13, lesson 15) — ✅ Implemented
- v1: causal dependency chain sweeps→disp→MSS→FVG→setup (`pipeline.py:182`, each object's `depends_on`).
- v2: made explicit as ordered checks (`pipeline.py structural_checks`): sweep→displacement→MSS→entry→setup→HTF-gate.
- DB: rendered as the ✓/✗/— step chain in `_candCard` (`dashboard.py:631`).

### 14. Entry — `[COURSE]`+`[RES]` (§14, lesson 12) — ✅ Implemented
- v1: entry = FVG CE (`setup.py:68`); **arm at k+1**, fill on a strictly later retrace (`fvg.py:69`).
- v2: `Entry.ref = ce` (`entry_models.py`); geometry via `assemble`. DB: entry fact + lifecycle.
- ℹ️ In-FVG entry location (touch / CE / far edge) is `[RES]`; CE is the chosen, documented default.

### 15. Stop — `[COURSE]`+`[NEC]` (§15) — ✅ Implemented (buffer intentionally deferred)
- v1: `stop = sweep.extreme` (`setup.py:68`); degenerate-stop floor `MIN_STOP_TICKS`/`min_stop_for`.
- v2: `S = sweep_extreme`; degenerate reject in `assemble`. DB: stop fact.
- ℹ️ `STOP_BUFFER` is a **DEFERRED** sentinel (needs instrument tick/noise analysis) — deliberately not guessed.

### 16. Targets & R:R — `[COURSE]` FROZEN B4 (§16) — 🟡 Partial (R:R architecture RESOLVED)
- v1 ✅: nearest opposing active ERL target (`setup.py:70`); **`MIN_RR=3.0` hard reject** (`setup.py:92`).
- **RESOLVED — three-concern separation (user decision 2026-08-27).** Structural validity, quality
  metrics, and course filters are **separate** concerns and must not be mixed. R:R is a **quality
  metric**, never a structural invalidation. The course's "minimum 1:3" is a **course filter**, so a
  setup reads: *Structure ✅ · Course filter (≥3R) ❌ · Recommendation: Skip*. v2 already keeps RR as a
  quality grade (`rr_quality`, `pipeline.py`; only RR≤1 blocks — itself a geometry sanity floor, not
  the 3R rule). **To build (filter layer):** an explicit, separate **course-filter stage** (first
  filter = ≥3R, lesson/§16), with the dashboard distinguishing Structure / Filters / Recommendation.
- 🟡 The **full target hierarchy** (opposing ERL · PDH/PDL · PWH/PWL · session H/L · equal H/L ·
  significant 15m+ swing · HTF) is **not walked** — only the nearest active ERL is used.
- DB: target + RR + quality badge (`dashboard.py`).
- **Gaps:** (a) build the course-filter layer (≥3R as a filter, + killzone filter from §11), (b) the ordered target hierarchy.

### 17. Daily bias & 4H context labels — `[COURSE]`+`[RES]` (§17, lesson 8/15) — 🟡 Partial
- ✅: bias computed; **HTF is not a hard veto** (`config.HTF_IS_VETO=False`); optional D/W anchor downgrades counter-trend 4H to neutral (`pipeline.py:280`).
- ❌: the explicit **labels** — *HTF-aligned / counter-context / possible-manipulation / possible-distribution* (`[RES:htf_labeling]`) — are **not produced**.
- DB: bias + anchor pills only.
- **Gap:** compute & display the four context labels per setup.

### 18. NWOG (New Week Opening Gap) — `[COURSE]`+`[NEC]` (§18) — ❌ Missing
- Config knobs only (`NWOG_KEEP`, `NWOG_REBALANCE_NOTE_PTS`); explicitly excluded from v1 liquidity; **no detector** anywhere in the three layers.
- ℹ️ The "small gaps tend to rebalance (≈≤60 pts)" line is a **course observation, not a law** — informational.
- **Gap:** NWOG detector (S/R / rebalance / magnet-target), tracked as IRL context, shown on DB.

### 19. ORG (Opening Range Gap) — `[COURSE]`+`[NEC]` (§19) — ❌ Missing
- Config knobs only (`ORG_TRACK_CURRENT_DAY_ONLY`, `ORG_REBALANCE_NOTE_PCT`); **no detector**.
- ℹ️ "~70% partial rebalance" is a course observation, not verified law — informational.
- **Gap:** current-day ORG detector + its 50% level, as context/target, shown on DB.

### 20. NO-TRADE is first-class — `[COURSE]` (§20) — ✅ Implemented
- v1: `recommend` returns NO-TRADE with per-setup reject reasons (`setup.py:122`).
- v2: staged NO-TRADE strings per cascade depth (`pipeline.py execution_for:492`); every candidate carries explicit `reasons` + `checks`.
- DB: NO-TRADE decision pill + reason list (`_candCard:649`).

### 21. Intraday trend change — `[COURSE]` (implied; §2/§10/§17) — ❌ Missing (proxies only)
- No dedicated detector. Proxies: MSS state machine (§9) and lifecycle opposing-MSS supersession / session expiry. `HTF_REVERSING` is a **DEFERRED** sentinel (a genuine HTF transition, not one isolated MSS).
- **Gap:** an explicit intraday trend-change / HTF-transition read (distinct from a single MSS).

### 22. Recommendation output & quality grade — `[COURSE map]`+`[RES]` (§ "Recommendation output") — 🟡 Partial
- ✅: structured output — v1 `Recommendation` (`setup.py:48`), v2 `Candidate.to_dict` (`pipeline.py:170`) + `snapshot` (`live.py:128`).
- ❌: an **A/B/C quality grade** (`QUALITY_BOUNDARIES` = DEFERRED). v2 substitutes an **RR-based** grade (reject/low/good/high).
- DB: RR quality badge + structured fact rows.
- **Gap:** decide the A/B/C scorer (course wants a quality grade) vs keep the RR grade (reconcile with §16).

---

## Gap summary

**❌ Missing (mechanize):**
1. Equal-highs/lows clustering (§3) & pools-as-zones (§3)
2. IRL classification / ERL-IRL labelling (§4)
3. Fib ladder 0/0.62/0.79/1 (§6)
4. Explicit AMD phase + consolidation/accumulation detector (§10)
5. ~~Session/killzone context wired into v2 + shown on DB (§11)~~ ✅ **DONE 2026-08-27**
6. HTF context labels — aligned / counter / possible-manipulation / possible-distribution (§17)
7. NWOG detector (§18)
8. ORG detector (§19)
9. Intraday trend-change / HTF-transition read (§21)
10. Ordered target hierarchy (§16)
11. Course-filter layer (§16 ≥3R + §11 killzone) with Structure/Filter/Recommendation separation
12. A/B/C quality grade — or a reasoned decision to keep the RR grade (§22)

**🟡 Partial (surface / complete in v2 + DB):**
- Market-structure read (HH/HL, reversal state) surfaced in v2/DB (§2)
- Full liquidity pool set + nested dealing-range hierarchy surfaced (§3, §6)

**✅ Resolved architectural decisions:**
- **§16 min 1:3 R:R (resolved 2026-08-27).** The engine separates **structural validity** / **quality
  metrics** (RR, P/D, liquidity distance) / **course filters**. R:R is a quality metric and never a
  structural invalidation; the course's ≥3R becomes a **course filter** → *Structure ✅ · Course filter
  (≥3R) ❌ · Recommendation: Skip*. Filter layer still to be built (§16 backlog item 11).

**✅ Implemented & faithful:** §1 MTF cascade, §5 premium/discount, §7 FVG lifecycle, §8 displacement,
§9 MSS, §12 sweep, §13 setup sequence, §14 entry, §15 stop, §20 NO-TRADE.

**ℹ️ Intentionally non-mechanical / documented gaps (leave as-is unless the course says otherwise):**
same-P/D-zone FVG tie-break; multi-bar displacement/reclaim; STOP_BUFFER; NWOG/ORG rebalance % as
*observations*; HTF-as-context-not-veto; in-FVG entry location (`[RES]`, CE chosen).

---

## Proposed incremental implementation order (for sign-off)

Ordered by faithfulness value × low risk (each is a self-contained increment; no engine `if model==`;
no PnL tuning; verify against the spec, not by search):

1. ~~**Sessions/killzones into v2 + DB** (§11)~~ ✅ **DONE 2026-08-27** — context tagged on every candidate + current-session chip on the V2 tab; killzone *filter* deferred to the filter layer.
2. **HTF context labels** (§17) — aligned/counter/possible-manipulation/possible-distribution from data v2 already has. Pure labelling. ← **NEXT**
3. **Equal-H/L clustering + pools-as-zones** (§3) and **surface the full pool set + nested ranges + fib ladder** (§3/§6) — liquidity/range completeness.
4. **NWOG & ORG detectors** (§18/§19) — new IRL context arrays; tracked, shown, usable as targets.
5. **IRL classification** (§4) — depends on 3+4 (needs the internal arrays first).
6. **Explicit AMD phase + consolidation detector** (§10) — the hardest; `[RES:amd_phase]`, needs its own mini-spec.
7. **Ordered target hierarchy** (§16) — replace nearest-ERL-only with the frozen hierarchy walk.
8. **Market-structure read surfaced** (§2) and **intraday trend-change** (§21) — structural visualization + transition read.
9. **Quality grade** (§22) — settle after §16 is reconciled.

**First decision needed:** the §16 R:R divergence (reconcile before it ripples through targets §16 and
grade §22). Everything else can proceed in the order above.
