# Porting the MT4 ICTv2 EA to the SCENARIO architecture (2026-08-28)

This document tells you how to update the MQL4 port so it matches the **current** logic of the Python
reference project. The Python side was re-architected on 2026-08-28; the MT4 port still implements the
**old** model and must be brought in line.

---

## 0. Where to read the reference code (do this first)

The authoritative implementation is the Python project **`ict_v2/`** in the same repo as this folder
(one directory up: `../` from `mt4_ict_v2/`). Full path on the dev machine:
`/Users/gil/trading 2.0/ict_v2/`.

Read these modules in this order — they are small and heavily commented; the comments ARE the spec:

| Read | File | What it defines |
|---|---|---|
| 1 | `ict_v2/pdarrays.py` | `PDArray` object + `role_of()` (lifecycle ≠ role, full auditable trace) |
| 2 | `ict_v2/liquidity.py` | `LiquidityObjective` — the ONE general liquidity abstraction + `collect_objectives` / `viable_targets` |
| 3 | `ict_v2/align.py` | `next_draw()` / `DrawObjective` — ERL/IRL class-aware draw (target) selection |
| 4 | `ict_v2/scenarios.py` | `Scenario` + `build_scenarios()` + `ScenarioBook` (creation / ranking / persistence / anti-churn) |
| 5 | `ict_v2/engine.py` | `MTFEngine` — the responsibility cascade (H4/H1 context → scenarios → M15/M1 execution) |
| 6 | `ict_v2/pipeline.py` | `htf_context`, `generate_candidates`, `execution_for_scenario`, `structural_checks` |

Tests double as executable specs — read/run them: `ict_v2/tests/test_pdarrays.py`,
`test_liquidity.py`, `test_scenarios.py`, `test_engine.py`.
Run everything: `cd "/Users/gil/trading 2.0" && .venv/bin/python -m pytest ict_v2/tests/ -q` (74 tests).

**See it live** (the clearest way to understand the target behavior): bring up the stack with
`./run-live.sh` (from the repo root) and open the dashboard **http://127.0.0.1:8010 → V2 tab**. Each
symbol shows the H4/H1 context, the 2–3 scenarios, and the per-symbol action verdict. Or rebuild just
the two services after a code change: `docker compose -f docker-compose.ict_live.yml up -d --build v2 dashboard`.

The living coverage map of every course rule → code is `ict_v2/docs/COURSE_COVERAGE.md`.

---

## 1. The conceptual shift (old → new)

**OLD (what the MT4 port does today):** a symmetric cascade where EVERY timeframe (4H, 1H, 15m, 1m)
tried to produce an FVG-based *candidate*, and each candidate ran a four-layer
`STRUCTURE / QUALITY / COURSE-FILTERS / RECOMMENDATION (TAKE/SKIP/WATCH)` model. A stage was "incomplete"
if no FVG formed on its displacement leg (the infamous "waiting for an H1 FVG").

**NEW (the target):** the cascade is organized by the **responsibility** of each timeframe:

```
4H  = STRATEGIC context   (bias · dealing range · P/D · equilibrium · ERL/IRL · pools BSL/SSL ·
                            HTF FVG draws · fib · NWOG/ORG)          — NO entries
1H  = INTRADAY context     (confirm the HTF bias OR establish the intraday direction via MSS;
                            + 1H pools/range/FVGs/fib)               — NO entries
        │  context completes on its STRUCTURAL READ — it NEVER waits for an FVG
        ▼
SCENARIO LAYER  = maintain the top 2 (max 3) STABLE market theses, built from ALL liquidity objectives
        ▼
15m = EXECUTION setup      (monitor: is a scenario retracing into its entry zone?)
1m  = EXECUTION trigger     (an entry-role PD array retraced into → that scenario is the trade)
```

Two ideas make this work and both must be ported:

- **FVG is a role-neutral PD ARRAY, not "the entry".** Its ROLE (draw / reaction / entry / inactive) is
  assigned by CONTEXT (timeframe + dealing-range position), *not* by its lifecycle. "No FVG on this leg"
  no longer means "no possible entry."
- **Liquidity is ONE general concept.** BSL/SSL, EQH/EQL, FVG, NWOG, ORG, fib are all just
  `LiquidityObjective`s with a `type`, `timeframe`, `liquidity_class (ERL/IRL)`, `strength`, and context
  `role`. The Scenario Layer ranks them all together. Adding a new PD array later = one more `kind`.

---

## 2. Component-by-component port targets

### 2.1 PD Array + role  →  `MQL4/Include/ICTv2/PDArrays.mqh` (extend) — ref `ict_v2/pdarrays.py`
A PD array is one object with **two independent attributes**:
- **lifecycle** `status`: `unfilled | touched | mitigated` (what price DID to it).
- **role** `role`: `draw | reaction | entry | inactive` (what it MEANS now) — assigned by `role_of()`.

`role_of(array, direction, zone, erl_irl)` decision (port this table exactly; see `pdarrays.role_of`):
- `tf_class`: `LTF` if TF ≤ 5m, `HTF` if TF ≥ 4H, else `MTF`.
- `side` vs the trade direction: long → *discount* = retrace side, *premium* = draw side; short mirrored.
- if `status == mitigated` → `inactive` (but a closed NWOG/ORG stays `reaction`, Lesson 13);
- else if side == retrace → `entry` when `LTF`, else `reaction` (Lesson 12: FVG is an entry tool only on 5m/1m);
- else if side == draw → `draw` when `HTF`, else `reaction`;
- else → `reaction`.
Emit the full auditable trace (tf_class / dealing_range_position / liquidity_class / seeking_vs_reacting /
side / lifecycle / rule / role) so a wrong role is diagnosable. **Role ≠ lifecycle** — a *unfilled* 1m FVG
in the discount is already the planned `entry`.

### 2.2 Liquidity objective  →  NEW `MQL4/Include/ICTv2/Liquidity.mqh` — ref `ict_v2/liquidity.py`
One struct for every liquidity kind: `{ kind(swing|eqhl|fvg|nwog|org|fib), tf, side(high/low),
price, top, bottom, liquidity_class(ERL/IRL), strength, role, status, label }`.
- `collect_objectives(context)` gathers swing pools (BSL/SSL) + FVG PD arrays + NWOG/ORG + fib 0.5/0.62/0.79
  from a context, tags each with ERL/IRL (inside vs outside the dealing range) and a `strength`.
- `strength = tf_weight × kind_weight × lifecycle_weight` — transparent RANKING weights (NOT course
  numbers; `[NEC]`). Copy the weight tables from `liquidity.py` (`_TF_WEIGHT`, `_KIND_WEIGHT`,
  `_LIFECYCLE_WEIGHT`). "Higher timeframe = stronger" (Lesson 6/11).
- `viable_targets(objectives)` = the DRAW candidates: kind ∈ {swing,eqhl,fvg,nwog,org} (NOT fib — fib is a
  retracement/reaction reference), still active (untaken), directional side, TF ∈ {HTF,MTF}.

### 2.3 Draw (target) selection — ERL/IRL aware  →  `Setup.mqh`/`Trade.mqh` — ref `ict_v2/align.py`
Lesson 10 alternation, **class first then objective**: when external liquidity (ERL) is taken price seeks
internal (IRL/FVG); when the internal imbalance is rebalanced price seeks external (ERL). `next_draw()`:
- if an opposing UNSWEPT external pool exists → target = that pool, `klass=ERL` (this is the old
  "nearest opposing pool" behavior — keep it);
- else → target = the nearest unfilled INTERNAL imbalance (FVG) on the draw side, `klass=IRL`.
Do **not** introduce a "nearest across all types by price" rule — decide the class first.

### 2.4 Scenario + ScenarioBook  →  NEW `MQL4/Include/ICTv2/Scenario.mqh` — ref `ict_v2/scenarios.py`
A **Scenario** = a thesis: `{ id, direction, draw(LiquidityObjective), entry_zone(low,high), rank,
rank_factors, state, why }`. The entry zone is the discount half (long) / premium half (short) of the
H4 dealing range: `long → (low, ce)`, `short → (ce, high)`.

**Creation** (`build_scenarios`): for each `viable_targets` draw make a scenario in the direction its side
implies (high→long, low→short). **Dedup by `scenario_id` keeping the strongest** (the same level often
appears on both 4H and 1H — keep the higher-TF one, or the set will churn).

**Ranking** — lexicographic, transparent (port the exact order): `alignment` (2 if matches intraday
direction, 1 if matches HTF bias, 0 else) → `class_fit` (1 if the draw's ERL/IRL matches the Lesson-10
next-seek class) → `strength` → `proximity`.

**Persistence / anti-churn (THE MOST IMPORTANT PART — the set must be STABLE):**
- `scenario_id = direction:draw-kind:round(draw-price):range-key`. Stable under price noise because it is
  keyed to structural LEVELS, not to ticks.
- The book is (re)built **only on a context (H4/H1) close** — never on an M1/M5 tick.
- **Membership changes only on a structural event:** the draw is taken/mitigated, the dealing range is
  superseded (range-key changed), or the draw disappears. Price noise churns NOTHING.
- **Hysteresis band:** admit a scenario when it ranks in the top **2 (target)**; drop it only when it
  falls past **3 (max)** or is invalidated. Asymmetric → no flapping between near-tied theses.
- `observe()` persists survivors IN PLACE (keep the same object, refresh rank/why), admits newcomers,
  drops invalidated ones. `monitor()` (called on M15/M1 closes) updates each active scenario's EXECUTION
  state WITHOUT touching membership.

### 2.5 Cascade engine  →  `Context.mqh` + `ICTv2_Cascade.mq4` — ref `ict_v2/engine.py`
- On **4H close** → build strategic context (`htf_context`), then rebuild the scenario book.
- On **1H close** → build intraday context (same `htf_context` on 1H — it IS context, no candidate/FVG
  requirement), then rebuild the scenario book.
- On **15m / 1m close** → `monitor` the scenarios (execution), never rebuild membership.
The context stages REUSE the same context builder; the 1H is no longer a "setup that needs an FVG".

### 2.6 Execution monitor + geometry  →  `Trade.mqh` — ref `pipeline.execution_for_scenario`
For each active scenario, from the entry candidates generated on the execution TF:
- take only candidates that are STRUCTURALLY VALID, geometry-**coherent**, and whose entry is inside the
  scenario's entry zone; prefer `entry_role == entry`.
- **coherent** = long: `stop < entry < draw`; short: `draw < entry < stop`.
- **geometry:** `entry` = the entry-role PD array CE; `stop` = the LTF manipulation (sweep) extreme;
  `target` = the SCENARIO's draw (not the candidate's own).
- reject degenerate stops with the per-instrument **min-stop floor** (`config.min_stop_for`) so you never
  surface an absurd-RR order.
- **states:** `triggered` (entry retraced into — FVG touched) → `armed` (entry PD array present, awaiting
  retrace; report the direction & distance price must move) → `retracing` (price in the zone, no entry
  array yet) → `watching` (price not in the zone).

---

## 3. Old → new mapping for the MQL4 files

| Old `.mqh` (current) | Action |
|---|---|
| `Swings.mqh`, `Structure.mqh`, `Structural.mqh`, `Sweep.mqh`, `Displacement.mqh`, `Ids.mqh` | **KEEP** — these are the v1 primitives (still the detectors). |
| `Context.mqh` | **REWORK** into the strategic (4H) + intraday (1H) context producers; add draws/pools/ERL-IRL/fib/NWOG/ORG. No candidates. |
| `EntryModels.mqh` | **REWORK**: FVG becomes a role-neutral PD array; add `role_of`. It is consumed as an *entry* only on the LTF in the retrace zone. |
| `Candidate.mqh`, `Setup.mqh`, `Quality.mqh` (the four-layer TAKE/SKIP/WATCH) | **SUPERSEDE**: replaced by the Scenario model + execution states. Keep `generate_candidates`-equivalent logic only as the LTF entry-candidate source the execution monitor filters. |
| NEW `Liquidity.mqh` | LiquidityObjective + collect + viable_targets. |
| NEW `Scenario.mqh` | Scenario + ScenarioBook (creation/ranking/persistence/anti-churn). |
| `Trade.mqh` | ERL/IRL `next_draw` + execution geometry (entry=CE, stop=manip extreme, target=scenario draw) + states. |

Update `docs/PORT_MAP.md` and `docs/V2_GAP.md` to reflect this, and add the new modules to the fixtures/
`verify_port.py` parity harness (generate expected values from the Python reference so the MQL4 output
matches byte-for-byte where deterministic).

---

## 4. What stays deferred (do NOT invent)

- **EQH/EQL** clustering has no course tolerance yet — leave it as a documented gap; scenarios draw on
  swings + FVG + NWOG/ORG for now.
- **Fib EXTENSION targets (1.5–4)** — projection direction is ambiguous; not implemented.
- **A/B/C quality grade** boundaries undefined — the RR grade is the interim.
- **STOP_BUFFER** — deferred (needs per-instrument tick/noise analysis).

The ranking weights and the 2/3 hysteresis band are `[NEC]` engineering values (the 2/3 came from the
user) — keep them explicit and identical to the Python reference so behavior matches.
