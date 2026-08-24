# ICT v2 — Multi-Timeframe (context / setup / execution)

**Status:** DESIGN (not yet built). Branch `v2-ict-mtf`. v1 (`ict_live/`) stays **frozen and untouched**.

## 1. Objective & principles

Implement the ICT workflow **as taught in the course** — three timeframes, each with a distinct job:

| Layer | Timeframe (default) | Job |
|---|---|---|
| **Context** | Higher (e.g. 4H / Daily) | bias, dealing range (premium/discount/EQ), liquidity objective (the draw) |
| **Setup** | Intermediate (e.g. 1H / 15m) | manipulation (liquidity sweep), displacement, MSS — **aligned to HTF context** |
| **Execution** | Lower (e.g. 5m / 1m) | entry FVG, precise entry/stop, target = HTF liquidity, management |

Binding rules for this project:
- **Faithful to the course, not our own interpretation.** The cross-TF confluence is the ICT method, not an invented heuristic.
- **v1 is frozen.** v2 **reuses v1 detectors by import only** (read-only) — it never modifies `ict_live/`.
- **No edge is claimed until validated.** Building v2 (Phases 0–4) is engineering; the edge is unproven until Phase 5 (pre-registered validation on fresh, unseen data). No PnL tuning on the way.
- **Minimal first.** Smallest faithful architecture that runs end-to-end; refine only against course examples, not open-ended search.

## 2. The three layers ↔ existing v1 components

Nearly every objective detector already exists and is **timeframe-parameterized** — v2 runs them on different TFs and adds the alignment between layers.

| v2 layer | Reused v1 components | Runs on |
|---|---|---|
| **Context (HTF)** | `swings` → `significance` → `swing_liquidity` (ERL pools = the draw) → `dealing_range` (premium/discount/EQ, direction=bias) | HTF bars |
| **Setup (MTF)** | `manipulation` (sweep) → `displacement` → `mss`, ranked via `ranking`; `lifecycle` for active/current | MTF bars, **gated by HTF context** |
| **Execution (LTF)** | `fvg` (+ `detect_fvgs_mtf` already does LTF entry inside an HTF displacement) → `setup` (entry/stop/target/RR) | LTF bars, within the MTF setup |
| **Cross-cutting** | `ids`, `ranking`, `lifecycle`, `reasoning` (why/deps graph), `features`/`outcomes`/`replay` (measurement) | all |
| **Data** | `market/bar_builder` (builds **all TFs from 1m**), `calendar`/`sessions`, `feeds/ingestor`, `storage/market_store` | — |
| **Existing MTF hooks** | `pipeline.analyze(structural_by_tf=…, refine_bars=…)`, `dealing_range.dealing_ranges(structural_by_tf)` | — |

## 3. Reuse vs new

**Reused wholesale (import, unchanged):** every `structure/*` detector, `bar_builder` multi-TF resampling, calendar/sessions, ingestor, market_store, `engine/reasoning|features|outcomes|replay`, and the live infra patterns (`runner`, `tracker`, `report`, `dashboard`, `control`, `notify`, `chart_render`).

**New in v2 (the actual work):**
1. `ict_v2/context.py` — run the context detectors on HTF → `HTFContext{bias, dealing_range, pd_zone(price), liquidity_targets}`.
2. `ict_v2/setup.py` — run manipulation/displacement/MSS on MTF, **gated by `HTFContext`** (bias + PD-zone + swept a context-relevant pool) → aligned setup candidates.
3. `ict_v2/execution.py` — LTF entry FVG inside the MTF displacement → entry/stop; target = HTF liquidity objective; RR gate.
4. `ict_v2/pipeline_mtf.py` — orchestrator: HTF→MTF→LTF, threading `reasoning` deps; returns a v2 recommendation.
5. `ict_v2/align.py` — the **cross-TF confluence rules** (§4). This is where v2's hypothesis lives.
6. v2 **execution/decision + exit** layer — its own, **not** v1's frozen `execution_quality`/fixed-2R (those were validated for v1's config).
7. v2 **live cadence** — evaluate on **LTF closes**; recompute context on HTF closes, setup on MTF closes.
8. v2 **validation harness** — pre-registration + fresh-data replay + expectancy/exit study + locked hold-out.

## 4. Cross-TF alignment rules (the ICT confluence — the hypothesis)

The heart of v2. To be pinned exactly at pre-registration; the shape:
- **Bias** = HTF dealing-range direction / structure (long / short / neutral).
- **Zone gate** = only longs while price is in the HTF **discount**, shorts in HTF **premium** (relative to HTF EQ).
- **Liquidity objective** = an HTF **ERL pool** (BSL for longs / SSL for shorts) = the draw = the target.
- **Setup** (MTF) = a **manipulation sweep** of an intermediate pool against retail, then **displacement + MSS in the HTF-bias direction**.
- **Execution** (LTF) = an **FVG** inside that displacement; entry at CE, stop beyond the manipulation extreme, target = the HTF liquidity objective; RR gate.

## 5. Cadence

- **Context** recomputed on each **HTF** close (held fixed between).
- **Setup** recomputed on each **MTF** close, against the current context.
- **Execution** evaluated on each **LTF** close → a setup fires **when the LTF triggers**, not once per HTF bar. (This is the deliberate departure from v1's 1H-close cadence, and part of what v2 must validate.)

## 6. Isolation & branch strategy

- All v2 code lives under **`ict_v2/`** on branch **`v2-ict-mtf`**. v1's `ict_live/` is imported read-only.
- A test asserts v2 never mutates v1 state and that **all v1 tests still pass** unchanged.
- v2 gets its **own** live service/dashboard entry (or a v2 tab) so it can run **alongside** frozen v1 for comparison, never replacing it.

## 7. Phased build plan (minimal-first)

- **Phase 0 — Scaffold.** `ict_v2/` package importing v1 detectors read-only; a v1-untouched guard test. *(this doc)*
- **Phase 1 — Context (HTF).** `context.py` → `HTFContext`; visualize (reuse chart/reasoning); verify on historical course scenes.
- **Phase 2 — Setup (MTF) gated by context.** `setup.py` + `align.py`; verify alignment on scenes.
- **Phase 3 — Execution (LTF) → full recommendation.** `execution.py` + `pipeline_mtf.py`; verify end-to-end on a handful of scenes.
- **Phase 4 — v2 live cadence + wiring.** LTF-close evaluation; v2 runner reusing tracker/report/dashboard/notify; runs alongside v1.
- **Phase 5 — Validation (the gate).** Pre-registration; fresh-data replay + expectancy/exit; **locked hold-out touched once**. Only on a pass is v2 "usable." No PnL tuning before this.

## 8. Decisions to pin at pre-registration (parameters, not a research cycle)

- Exact TF triad (e.g. 4H→15m→1m vs Daily→1H→5m).
- Alignment thresholds (PD-zone boundaries, which liquidity pool = objective, sweep/displacement/MSS definitions per TF — reuse v1's where they carry over).
- v2 execution filter + exit model (fresh — do not inherit v1's frozen ones as validated).
- Go/No-Go metrics + locked hold-out window (fresh, never-seen data).
