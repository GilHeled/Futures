# ICT Course → v2 Code & Dashboard Coverage Map

**Purpose.** v2's objective is to faithfully mechanize **every rule this course teaches** — and to let
you point at any mechanical rule and see exactly where it lives in code and on the dashboard. This is
the living map. Each rule is classified and anchored to its implementation across the three layers.

## Authoritative source

> **UPDATE 2026-08-27 — the raw course is available.** The full `Trade/` lesson set is on disk at
> `~/Library/Mobile Documents/com~apple~Pages/Documents/Trade/` (lessons 3,4,5,6,8,9,10,11,12,13,14,15,16
> as PDFs). These are the **primary authority**; the distillation below is secondary. This map is now
> being verified **lesson by lesson** against the PDFs (read with `pypdf`; Hebrew, RTL). Verified so far:
> **Lesson 6** (Liquidity), **Lesson 8** (Advanced Market Structure — the fib source), **Lesson 9**
> (Premium/Discount). Reading them already corrected the distillation (see §6 fib orientation, and the
> new rules logged at the end).

The course rules are taken from the raw lessons above, cross-checked with the in-repo distillation:

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

### 2. Market structure — `[COURSE]` (§2, **Lesson 15 verified**) — ✅ Implemented (trend read surfaced)
- Lesson 15 verified: uptrend = higher highs AND higher lows; downtrend = lower highs AND lower lows;
  a high that fails to break the prior high (or a strong level + turn) = a **potential** trend change;
  the opposite HH/HL sequence actually forming = a **confirmed** trend change.
- v1 ✅: fractal swings + confirm-width (`swings.py`), HH/HL/LH/LL skeleton + dominant/broken/protected (`significance.py`); potential/candidate/confirmed carried by MSS states (§9).
- v2 ✅: `trend_state(ms)` (`pipeline.py`) is a VERDICT over v1's skeleton + MSS (no new detection) →
  `trend` up/down/none (last-two HH/HL rule) + `change` confirmed/potential/"". Surfaced on `HTFContext`
  (`.trend`, `.trend_change`) + `snapshot.context.trend[_change]`; DB shows "trend up · potential/confirmed"
  in the 4H context read. Verified `test_trend_state_verdict`.
- ℹ️ Per-candidate MSS state is also shown in the candidate chain (the structural break itself).

### 3. Liquidity pools / BSL/SSL — `[COURSE]`+`[NEC]` (§3, **Lesson 6**) — 🟡 Partial (full set now surfaced)
- Lesson 6 verified: liquidity = stops above highs / below lows; **BSL** = high swing (stops above),
  **SSL** = low swing (stops below); *identify by equal highs/lows*; **larger TF ⇒ stronger**; and a
  hard rule — **do NOT mark liquidity below the 15-minute chart** (logged as a new rule below).
- v1 ✅(core): period/session pools `liquidity.py:42` (PDH/PDL, PWH/PWL, Asia/London/NY-AM/NY-PM), swing BSL/SSL `swing_liquidity.py:34`.
- v2 ✅: the **full pool set** is now surfaced — `snapshot.context.pools` (BSL/SSL + price) from `context.liquidity` (`live.py`); DB shows a `pools N` chip (all pools in tooltip). Verified `test_full_pool_set_and_nested_ranges_surfaced`.
- 🟡 **Pending, parameter-unresolved (do NOT guess):** (a) **equal-highs/lows clustering** — Lesson 6
  teaches equal H/L as liquidity but gives **no numerical tolerance**; (b) **pools-as-zones** (± band)
  — needs a width tolerance. Both are *mechanically required but the course provides no number*; left
  pending an explicit course rule or a deliberately-approved `[NEC]` parameter. **Not** using 0.15·ATR.

### 3b. Support / Resistance levels — **Lesson 11** — ✅ Implemented (re-framing; NO new mechanic)
Lesson 11 verified end-to-end. It teaches S/R as **the areas where traders' stops/targets rest** and
enumerates the S/R *types* (page 2) — and every one is **already implemented under another name**, so
per the no-duplicate-logic rule this is documented, not re-coded:

| Lesson-11 S/R type | Already implemented as |
|---|---|
| liquidity areas / old highs & lows | v1 swing pools → `active_erl` (§3) |
| equal highs / lows | ⛔ the one **gap** — equal-H/L clustering (§3), parameter-blocked |
| imbalance candle (FVG) on a high TF | FVG (§7) |
| Asia / London high & low | v1 session pools `liquidity.py` (§3) |
| weekly / daily candle high & low | PWH/PWL, PDH/PDL `liquidity.py` (§3) |

The one **distinct rule** — *"mark ONLY untaken (unbroken) levels; once taken, a level loses meaning"* —
is exactly `active_erl = active(pools)` = pools with `not swept` (`swing_liquidity.py:55`), which is
what v2 surfaces as the pool set / draw. **Verified.** "Higher interval = stronger" is already in v1
ranking + the ≥15m rule. Equal H/L is again taught as a *price ZONE* with **no numerical tolerance** —
reinforces the parameter-block, nothing to invent. DB: the pools chip is now labelled "untaken S/R
levels (Lesson 11)". No new detector, no test (no new logic).

### 4. ERL vs IRL — `[COURSE]` (§4, **Lesson 10**) — ✅ Implemented
- Lesson 10 verified: **ERL** = liquidity ABOVE the range high / BELOW the range low (untaken equal
  highs/lows the market draws to); **IRL** = BETWEEN low and high (FVG/gaps/imbalance — rebalance area).
  Classification is relative to the active (fib) dealing range; ERL-broken → draw to IRL, IRL-filled →
  draw to ERL. (The lesson's two-things rule — price draws to highs/lows OR returns to imbalance — is
  the conceptual model behind the classification.)
- v2 ✅: `HTFContext.erl_irl(price)` → `"ERL"`/`"IRL"` vs the dealing range. Every surfaced pool is
  tagged `loc` (`live.py`), and NWOG/ORG mids too; DB shows an `ERL n / IRL m` breakdown on the pools
  chip + `[ERL]/[IRL]` per pool in the tooltip. Verified `test_erl_irl_classification`.

### 5. Premium / Discount — `[COURSE]` (§5, lesson 9) — ✅ Implemented
- v1: `dealing_range.py:40 zone_of` (premium/discount/EQ, CE `:59`).
- v2: gate requires longs in discount / shorts in premium (`align.py:41`); entry P/D tagged (`pipeline.py:392`).
- DB: `P/D` fact in the candidate card (`dashboard.py:646`).

### 6. Fibonacci / dealing range — `[COURSE]` (§6, **Lesson 8**) — ✅ Implemented
- Lesson 8 verified (the fib source): main S/R levels **0 / 0.5 / 0.62 / 0.79 / 1** (0.5 = equilibrium;
  0.62/0.79 = OTE). **Orientation:** UPtrend → 0 at the HIGH, 1 at the LOW; DOWNtrend → 0 at the LOW,
  1 at the HIGH. (The distillation didn't carry the orientation — reading the lesson fixed it.)
- v2 ✅ fib ladder: `HTFContext.fib_levels()` (`pipeline.py`, `FIB_LEVELS`) → the 5 levels with course
  orientation + premium/discount tag; surfaced in `snapshot.context.fib`; DB renders a fib row
  (equilibrium/OTE colour-coded). Verified `test_fib_ladder_levels_and_orientation`.
- v2 ✅ nested ranges: each cascade stage's dealing range (4H/1H/15m, `source_tf`-tagged) is exposed —
  `MTFSetup.dealing_range` + `snapshot.dealing_ranges`; DB renders a `ranges` row. Verified.
- v1 basis: dealing range = most-recent completed opposing structural leg (`dealing_range.py:48`), each `source_tf`-tagged.

### 7. FVG lifecycle & TF rules — `[COURSE]` (§7, **Lesson 12 verified**) — ✅ Implemented (one documented gap)
> **Lesson 12 verified against the raw PDF.** Every rule matches our implementation: geometry (bullish
> high₁<low₃ / bearish low₁>high₃); **body-close mitigation** ("closed when a candle closes it with its
> body"); **P/D eligibility** (mark the FVG in the discount for longs / premium for shorts — the area
> price returns to); **higher interval = stronger**; FVG marked on 5m/1m **only as part of the
> entry/exit strategy** (v2 uses 1m as trigger, 5m as optional refine). CE (50%) entry is our chosen
> in-FVG location (`[RES:fvg_entry_loc]`), consistent with the lesson. **One nuance, capability-present
> / parameter-undefined:** Lesson 12 says a *"very large"* FVG should be re-marked on a smaller interval
> — v2 has the optional MTF entry-refinement mode (`ICT_V2_REFINE`) that provides this capability, but
> the **auto-trigger threshold ("very large") is not quantified by the course**, so it is not
> auto-fired (parameter-blocked; refinement stays an explicit mode). No new mechanic; no code change.
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

### 10. Power of 3 / AMD — `[COURSE]` (§10, **Lesson 16 verified**) — 🟡 Mostly (1 parameter-block)
- Lesson 16 verified: three phases — **accumulation** (consolidation around/below the open) →
  **manipulation** (the counter-move that takes liquidity / stops, often London) → **distribution**
  (the real move WITH the main trend, toward equal H/L + FVG; break of an old high = exit). The lesson
  states the transition explicitly: *look for the intraday trend change* → the confirmed MSS is when
  manipulation gives way to the real move.
- ✅ Mechanized: **ranked candidate sweeps** + **lexicographic ranking** (`ranking.py`) + **supersession/
  session expiry** (`lifecycle.py`) + "don't trade the first sweep" (emergent); the **manipulation** =
  the sweep anchor (§12); the **distribution** = the displacement toward the draw (§8/§16).
- ✅ **AMD-phase label** (NEW, Lesson 16): `amd_phase(direction, bias, mss_state)` → `manipulation` /
  `distribution` (transition = confirmed MSS aligned with bias). Tagged on every `Candidate.amd_phase`,
  serialized, shown as a chip in the candidate card. Verified `test_amd_phase`. This also provides §17's
  `possible-manipulation` / `possible-distribution` reads (which §17 deferred here).
- ⛔ **Parameter-block (STOP):** the **accumulation / consolidation detector** — Lesson 16 describes
  consolidation around the open but gives **no range/duration threshold** (`config.CONSOLIDATION_DETECTOR`
  is a hard sentinel; `[RES:amd_phase]`). Not invented. `amd_phase` never emits `accumulation`. Awaiting
  an explicit course rule or an approved `[NEC]`.

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
- ✅ **RESOLVED + BUILT — the four-layer semantic model (2026-08-27, `ict_v2/recommend.py`).** Structure
  / Quality / Course-Filters / Recommendation are now separate. R:R is a **quality** metric (never
  structural); the course's ≥1:3 is a **course filter** (`min_rr=3.0`), so a setup reads exactly
  *Structure ✅ · Course filter (≥3R) ❌ · Recommendation SKIP*. RR no longer gates structure at all
  (the old RR≤1 block is gone). Dashboard shows all four layers per candidate.
- 🟡 The **full target hierarchy** (opposing ERL · PDH/PDL · PWH/PWL · session H/L · equal H/L ·
  significant 15m+ swing · HTF) is **not walked** — only the nearest active ERL is used.
- DB: target + RR + quality badge (`dashboard.py`).
- **Gaps:** (a) build the course-filter layer (≥3R as a filter, + killzone filter from §11), (b) the ordered target hierarchy.

### 17. Daily bias & 4H context labels — `[COURSE]`+`[RES]` (§17, lesson 8/15) — 🟡 Partial (alignment labels done)
- ✅ Labels: `context_label(direction, bias)` → `htf-aligned` / `counter-context` / `neutral-context`
  (`pipeline.py`); every `Candidate` tagged `.context_label`, serialized, shown as a chip in the
  candidate-card header (`dashboard.py` `.clabel`). `CONTEXT_LABELS` vocab carries all five values.
- ✅ The **AMD-phase read** — `possible-manipulation` / `possible-distribution` — is now provided by
  §10's `amd_phase` (Lesson 16): manipulation → distribution, transition = confirmed MSS. Surfaced as a
  separate per-candidate chip (kept distinct from the alignment label, since they are two axes).
- ✅ **Veto contradiction RESOLVED (2026-08-27).** The old `align.gate_setup` bias/P/D veto is **removed**
  (function deleted). HTF bias is now a QUALITY label (`context_label`) and premium/discount a quality
  metric — neither invalidates structure nor blocks a recommendation. A counter-context setup can be
  TAKE (verified: 11/16 demo TAKEs are counter-context). HTF is context, exactly as §1/§17 intend.
- ✅ AMD-phase labels provided by §10 `amd_phase` (possible-manipulation / possible-distribution).
- Verified: `test_htf_context_labels`, `test_four_layer_semantics_and_no_bias_veto`, `test_align`.

### 18. NWOG (New Week Opening Gap) — `[COURSE]` (§18, **Lesson 13**) — ✅ Implemented
- Lesson 13 verified: NWOG = gap between the last price before the weekend (Fri close) and the new
  trading-week open; a **magnet/target** + **support/resistance**, explicitly **NOT liquidity/ERL**;
  mark **three**, keep the marking after close, additionally keep an **unclosed** gap **older than 3
  weeks**. (The ≤60/>60/>200-pt rebalance figures are LIVE-session *statistics* → informational, not encoded.)
- v2 ✅: `pdarrays.nwogs(bars, keep=3)` (weekend = >24h bar gap; 'closed' = body close back through the
  Friday-close edge, Lesson-12 convention; keeps 3 + old-unclosed>3wk). Surfaced `snapshot.context.nwog`
  (top/bottom/mid/closed/age); DB renders an `NWOG` row (closed struck-through). Runs off the 4H
  context buffer (~8 weeks). Verified `test_pdarrays.py` (detection, midpoint, closed, retention).

### 19. ORG (Opening Range Gap) — `[COURSE]` (§19, **Lesson 14**) — ✅ Implemented
- Lesson 14 verified: ORG = gap between the **prior day's close (16:15 ET)** and the **current day's
  open (09:30 ET)**; the **50% midpoint** is the key line; **current day only**; magnet/target that
  identifies the day's opening potential. (The "~70% close ≥half" figure is an *observation* → not encoded.)
- v2 ✅: `pdarrays.org(bars)` (ET/DST-correct 09:30 open vs prior-day ≤16:15 close; 50% midpoint;
  'closed' = body close back through the prior-day-close edge). Surfaced `snapshot.context.org`; DB
  renders an `ORG` row with the 50% level. Runs off the 15m confirm buffer (09:30/16:15 land on 15m
  boundaries; ~2.5-day span). Verified `test_pdarrays.py` (midpoint 150 example, closed, guards).

### 20. NO-TRADE is first-class — `[COURSE]` (§20) — ✅ Implemented
- v1: `recommend` returns NO-TRADE with per-setup reject reasons (`setup.py:122`).
- v2: staged NO-TRADE strings per cascade depth (`pipeline.py execution_for:492`); every candidate carries explicit `reasons` + `checks`.
- DB: NO-TRADE decision pill + reason list (`_candCard:649`).

### 21. Intraday trend change — `[COURSE]` (§21, **Lesson 15 verified**) — ✅ Implemented
- Lesson 15: intraday trend change is the SAME trend-change model (§2) on a small interval (1m/5m/15m),
  occurring at high-liquidity areas (equal H/L, HTF FVG) with an energetic/momentum move.
- v2 ✅: the `trend_state` verdict (trend + potential/confirmed change) is computed from each analyzed
  TF's structure/MSS — surfaced for the context, and the intraday stages (1H/15m/1m) already carry
  their MSS state (potential→candidate→confirmed) per candidate = the intraday structure break. The
  energetic-move requirement is the displacement (§8) already in the chain.
- ℹ️ `config.HTF_REVERSING` (a *major* HTF regime transition beyond one MSS) stays DEFERRED — that is a
  distinct, larger concept than Lesson 15's trend-change and the course gives no threshold for it.

### 22. Recommendation output & quality grade — `[COURSE map]`+`[RES]` (§ "Recommendation output") — 🟡 Partial
- ✅: structured output — v1 `Recommendation` (`setup.py:48`), v2 `Candidate.to_dict` (`pipeline.py:170`) + `snapshot` (`live.py:128`).
- ❌: an **A/B/C quality grade** (`QUALITY_BOUNDARIES` = DEFERRED). v2 substitutes an **RR-based** grade (reject/low/good/high).
- DB: RR quality badge + structured fact rows.
- **Gap:** decide the A/B/C scorer (course wants a quality grade) vs keep the RR grade (reconcile with §16).

---

## Lessons 3 / 4 / 5 — basics & verification (no new mechanic)
- **Lesson 3 (trading orders)** — the 4 order types (Market / Limit / Stop-Limit / Stop-Loss) and
  passive-vs-aggressive execution. Order-mechanics education, **informational**. Our entry is a resting
  **limit** at the FVG (arm k+1, fill on a later retrace, §14), which is exactly the passive-limit model.
- **Lesson 4 (contract names & rollovers)** — contract naming, "trade the most-liquid (front) contract",
  3-month rollovers (CME roll dates), and a caution that **roll week is noisy** ("less speculation, more
  bureaucracy"). Data/instrument-layer, **informational**. The roll-week caution is a *possible* future
  **course filter** (skip trading during roll week) but needs the CME roll calendar and is stated as a
  caution, not a hard rule — logged, not built.
- **Lesson 5 (sessions/killzones)** — **verifies §11**: London recommended window 02:00–05:00 ET matches
  `config.SESSIONS["london_active"]`; Asia = overnight consolidation, London breaks Asia's range (ties to
  the AMD manipulation, Lesson 16). Already implemented (§11); this confirms the windows.

> **Lesson coverage:** all lessons present on disk (3,4,5,6,8,9,10,11,12,13,14,15,16) have now been read
> and represented. Lessons **1, 2, 7 are not on disk** (numbering gaps / intro not provided) — flagged;
> re-verify if they are added.

## New course rules found by reading the raw lessons (not in the distillation)

Reading the PDFs surfaced concrete mechanical rules the 20-section distillation had dropped or blurred:

- **≥15-minute liquidity floor** (Lesson 6 & 8) — ✅ **DONE 2026-08-27**: `tf_minutes()` +
  `assert_liquidity_floor()` enforce that the context/setup/confirmation TFs are ≥15m; `MTFEngine`
  rejects a <15m structural TF at construction. The 1m trigger (and refine) may be finer — they only
  trigger, they do not mark liquidity. Verified `test_liquidity_floor_15m_and_pullback`.
- **≥50% pullback rule** (Lesson 8) — ✅ **DONE 2026-08-27**: `pullback_pct(disp, entry)` measures the
  entry's retrace depth into the displacement leg; surfaced as a QUALITY metric on each candidate
  (≥0.5 = adequate) + a dashboard chip. A quality read, not a gate (kept faithful to "context/quality").
- **Fib EXTENSION targets 1.5 / 2 / 2.5 / 3 / 3.5 / 4** (Lesson 8): multiples of the 0→1 range, used
  for *target-taking*. Directly relevant to §16 targets. v2 status: **missing** → fold into the target
  hierarchy work (§16). This is a course-grounded target mechanism we did not previously have.

## Gap summary

**❌ Missing (mechanize):**
1. Equal-highs/lows clustering (§3) & pools-as-zones (§3) — **parameter-blocked** (Lesson 6 teaches them but gives no tolerance; pending explicit rule or approved `[NEC]`; **not** 0.15·ATR)
2. ~~IRL classification / ERL-IRL labelling (§4)~~ ✅ **DONE 2026-08-27** (Lesson 10)
3. ~~Fib ladder 0/0.5/0.62/0.79/1 (§6)~~ ✅ **DONE 2026-08-27** (Lesson 8, with orientation)
4. Explicit AMD phase + consolidation/accumulation detector (§10)
5. ~~Session/killzone context wired into v2 + shown on DB (§11)~~ ✅ **DONE 2026-08-27**
6. HTF context labels (§17) — 🟡 alignment labels ✅ **DONE 2026-08-27**; AMD-phase labels pending §10; bias-veto removal pending item 11
7. ~~NWOG detector (§18)~~ ✅ **DONE 2026-08-27** (Lesson 13)
8. ~~ORG detector (§19)~~ ✅ **DONE 2026-08-27** (Lesson 14)
9. Intraday trend-change / HTF-transition read (§21)
10. Ordered target hierarchy (§16) — now includes the **fib EXTENSION targets 1.5–4 (Lesson 8)**
11. Course-filter layer (§16 ≥3R + §11 killzone) with Structure/Filter/Recommendation separation
12. A/B/C quality grade — or a reasoned decision to keep the RR grade (§22)
13. ≥15m liquidity floor (assert) & ≥50% pullback rule (Lesson 6/8) — new rules to represent
14. **Full lesson-by-lesson re-verification** of every section against the raw PDFs (started: L6/L8/L9)

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
2. ~~**HTF context labels** (§17)~~ ✅ **DONE 2026-08-27** (alignment axis) — AMD-phase labels come with §10; bias-veto removal is item 11.
3. **Liquidity/range completeness** (§3/§6): ✅ **DONE 2026-08-27** the parameter-free parts — fib ladder (Lesson 8, oriented), full pool set surfaced (Lesson 6), nested per-stage ranges surfaced. ⛔ **Deferred (parameter-blocked):** equal-H/L clustering + pools-as-zones — no course tolerance; awaiting an explicit rule or an approved `[NEC]` value. ← resume here when the tolerance is decided
4. ~~**NWOG & ORG detectors** (§18/§19)~~ ✅ **DONE 2026-08-27** (Lessons 13/14) — `ict_v2/pdarrays.py`, surfaced on the V2 tab. IRL-classification usage (§4) can now build on these.
5. ~~**IRL classification** (§4, Lesson 10)~~ ✅ **DONE 2026-08-27** — `HTFContext.erl_irl()`, pools + NWOG/ORG tagged, DB breakdown.
6. ~~**Support/resistance levels** (Lesson 11)~~ ✅ **DONE 2026-08-27** — re-framing of existing liquidity concepts; no new mechanic (documented §3b).
7. **Lesson-by-lesson sweep of the remainder** (user mandate: continue without per-lesson approval, stop only on genuine ambiguity or an undefined parameter):
   - ~~**Lesson 12 (FVG)**~~ ✅ **DONE 2026-08-27** — verified; matches §7; nuance ("very large FVG → smaller TF") is capability-present/param-undefined.
   - ~~**Lesson 15 (intraday trend changes)**~~ ✅ **DONE 2026-08-27** → §2 + §21 via `trend_state` (verdict over v1 skeleton+MSS).
   - ~~**Lesson 16 (Power of 3)**~~ ✅ **DONE 2026-08-27** → §10 AMD phase (manipulation/distribution); accumulation detector PARAMETER-BLOCKED (stop).
   - ~~**Lessons 3, 4, 5**~~ ✅ **DONE 2026-08-27** — informational (3/4) / verification of §11 (5); no new mechanic.
   - **ALL AVAILABLE LESSONS NOW READ & REPRESENTED.** Remaining work is cross-lesson SYNTHESIS, not new lessons:
     - ~~**Course-filter layer**~~ ✅ **DONE 2026-08-27** (`ict_v2/recommend.py`) — Structure / Quality / Course-Filters / Recommendation (TAKE/SKIP/WATCH); ≥3R + killzone as filters; §17 bias-veto removed; dashboard shows all four layers. ← the big architectural piece, landed.
     - ~~Assert **≥15m liquidity floor** + **≥50% pullback** rules (§2/§3/§6)~~ ✅ **DONE 2026-08-27**.
   - ⛔ **DEFERRED — for the separate "parameter-dependent" review the user flagged:**
     - **Ordered target hierarchy** (§16): a **DATA gap** — v2's cascade (`v1.analyze`) exposes only
       swing-type ERL pools (`SwingPool`: kind+price, no type); the typed pools the §16 hierarchy orders
       by (PDH/PDL, PWH/PWL, Asia/London H/L) live in v1's **live-only `LiquidityRegistry`** (`Pool` with
       name/source), not in the analyze output. Implementing the typed hierarchy needs those pools merged
       into the v2 context (a data-layer task), not a parameter. **Fib EXTENSION targets 1.5–4** (Lesson 8)
       have an unresolved projection-direction (the lesson doesn't crisply fix which way to project for a
       target) — an ambiguity, not to be guessed.
     - **A/B/C quality grade** (§22): boundaries are `[RES]` / `config.QUALITY_BOUNDARIES = DEFERRED` — an
       **undefined parameter**. v2 uses the RR-based grade (reject/low/good/high) as the interim quality read.
     - equal-H/L clustering + pools-as-zones (§3); accumulation/consolidation detector (§10) — undefined tolerances.
4. **NWOG & ORG detectors** (§18/§19) — new IRL context arrays; tracked, shown, usable as targets.
5. **IRL classification** (§4) — depends on 3+4 (needs the internal arrays first).
6. **Explicit AMD phase + consolidation detector** (§10) — the hardest; `[RES:amd_phase]`, needs its own mini-spec.
7. **Ordered target hierarchy** (§16) — replace nearest-ERL-only with the frozen hierarchy walk.
8. **Market-structure read surfaced** (§2) and **intraday trend-change** (§21) — structural visualization + transition read.
9. **Quality grade** (§22) — settle after §16 is reconciled.

**First decision needed:** the §16 R:R divergence (reconcile before it ripples through targets §16 and
grade §22). Everything else can proceed in the order above.
