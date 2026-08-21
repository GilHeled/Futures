# TradingView MCP — Integration Proposal (for approval)

Status: **PROPOSAL — awaiting approval. No integration code written yet.**
Scope: `tradesdontlie/tradingview-mcp` as a **development / debugging / visual-fidelity tool only.**
It is **never** a market-data source and **never** an engine dependency. The frozen methodology
and the webhook→raw-1m→resample→engine pipeline are unchanged.

---

## 1. What the MCP actually is

- **Connection:** drives the **TradingView *Desktop* app** (Electron/Chromium) over the **Chrome
  DevTools Protocol** on `localhost:9222`. You must launch the desktop app with
  `--remote-debugging-port=9222`. It is **local UI automation of undocumented internals** — not
  an API, not a data feed.
- **Requirements:** TradingView Desktop + **paid** subscription; Node.js 18+; the repo cloned and
  added to an MCP config. macOS/Windows/Linux.
- **License:** MIT (code only; grants nothing re: TradingView's data/IP).
- **Vendor's own warnings (quoted):** *"accesses undocumented internal TradingView application
  interfaces"*, *"can change or break without notice in any TradingView update"*, and use *"may
  conflict with their Terms of Use."*

This is **exactly** the profile you specified: fine as a developer's eyes-and-hands on the chart,
unacceptable as a source of truth. The proposal hard-enforces that boundary.

## 2. Capability inventory (84 tools) vs. what you asked for

Everything on your "what I want Claude to do" list is supported:

| You asked for | MCP tool(s) | Notes |
|---|---|---|
| Inspect current chart | `chart_get_state`, `quote_get`, `capture_screenshot` | symbol + TF + active indicators |
| Change symbol | `chart_set_symbol` | e.g. `ES1!`, `NQ1!` |
| Change timeframe | `chart_set_timeframe` | `1,5,15,60,D,W,M` — maps to our TF roles |
| Jump to historical date | `chart_scroll_to_date`, `chart_set_visible_range` | ISO date / unix range |
| Control Bar Replay | `replay_start` (at date), `replay_stop` | |
| Step replay forward | `replay_step`, `replay_autoplay` | one bar / timed autoplay |
| Pause/resume replay | `replay_autoplay` (start) + step (manual) ; `replay_status` | no explicit "pause" verb — autoplay off = paused |
| Capture screenshots | `capture_screenshot` | regions: full / chart / strategy_tester |
| Read visible chart info | `chart_get_state`, `data_get_ohlcv`, `data_get_study_values` | |
| Read Pine indicators | `data_get_pine_lines/labels/tables/boxes`, `data_get_study_values` | reads `line/label/table/box.new()` output |
| Inspect drawing objects | `draw_list`, `draw_remove_one`, `draw_clear` | |
| **Draw on chart** | `draw_shape` (`horizontal_line`, `trend_line`, `rectangle`, `text`), `draw_list/remove/clear` | **only these 4 primitives** |

Plus useful extras: `batch_run` (sweep symbols/TFs), `pane_*`/`tab_*` (side-by-side engine-vs-chart),
`ui_evaluate` (arbitrary JS escape hatch), `tv_health_check`/`tv_launch`.

## 3. Which capabilities matter for this ICT project

**Tier 1 — the fidelity workflow (the whole point):**
`chart_set_symbol` · `chart_set_timeframe` · `replay_start/step/stop` · `capture_screenshot` ·
`draw_shape`/`draw_clear` · `data_get_ohlcv`. These let Claude put the chart at exactly the bar the
engine is reasoning about, overlay the engine's interpretation, and screenshot it.

**Tier 2 — cross-checks & convenience:** `data_get_pine_*` (if you keep an ICT Pine indicator on the
chart, we can compare *its* levels to the engine's), `chart_get_state`, `batch_run`, panes/tabs.

**Tier 3 — ignore for now:** `alert_*`, `watchlist_*`, `replay_trade` (paper trades — we don't
execute), `pine_save`/cloud, streaming (`tv_stream_*`) — streaming is a live-data path we
deliberately do **not** use as truth.

### Drawing the 15 ICT primitives with only 4 shape types
All expressible — no gap:

| ICT object | Drawn as |
|---|---|
| Active Dealing Range | `rectangle` (leg span) + `horizontal_line` at endpoints |
| Premium / Discount | `horizontal_line` at 50% (CE) + optional shaded `rectangle` halves |
| ERL / Liquidity pools | `horizontal_line` per pool + `text` label (PDH/PWL/ASIA_H…) |
| IRL (FVG / NWOG / ORG) | `rectangle` (gap box) + `horizontal_line` at CE |
| Sweep | `text` marker at the swept level + swept wick |
| Manipulation Extreme | `horizontal_line` + `text` |
| Displacement | `trend_line` along the impulse leg |
| MSS | `horizontal_line` at the broken swing + `text "MSS"` |
| Entry / Stop / Target | three `horizontal_line`s + `text` (color by role) |
| Target liquidity | `horizontal_line` + `text` |
| AMD phase / HTF context | `text` labels |

## 4. Limitations & unknowns (must verify before trusting overlays)

**Hard limitations:**
- **Paid desktop app + debug flag required**; breaks on TradingView updates (vendor-stated).
- **Only 4 drawing primitives** — no native Fib tool; we synthesize P/D from lines+rectangle.
- **No real pause verb** — "paused" = autoplay stopped; we step manually.
- **ToU friction** — undocumented internals; acceptable for private local dev, not for anything shipped.

**Unknowns the README did not answer — RESOLVED by the Phase-0 probe (2026-08-21, TV Desktop
3.3.0 / Electron 38 / Chrome 140; MNQ1! @ 1h; replay 2026-08-14):**

| # | Question | Finding | Verdict for causal work |
|---|---|---|---|
| 1 | `draw_shape` anchoring | **(time, price) fully supported.** Every shape point is `{time, price}`; `draw get <id>` round-trips the exact times/prices set. `draw list` is minimal (id+name only) — use `draw get` for geometry. **All** shapes require a finite `--time` (even `horizontal_line`: omitting time → `point.time must be finite` error). Rich style `properties` (color/fill/text/extend) available. | **SAFE — TIME+PRICE** |
| 2 | `ohlcv` format | **Unix epoch seconds**, field `time`, **ascending**; `bars[]` (full, + `total_available`) / `last_5_bars[]` (summary). Timezone is epoch (UTC instant); alignment to our ET session bars is a *data-fidelity cross-check* to run at capture, not a format ambiguity. | **SAFE (format)**; tz alignment = must-verify |
| 3 | `ohlcv` vs replay cursor | **Truncated at the cursor and advances in lockstep.** Realtime returns bars to 2026-08-21; during replay it returned only ≤ cursor and stepped 23:00→00:00→01:00→02:00 as the cursor advanced. No future leak. | **SAFE** |
| 4 | Future-leak across tools during replay | `ohlcv` **SAFE**; `quote` **SAFE** (respects cursor); `screenshot` **SAFE** — future region is blank right of the cursor (verified visually), with a *chart-state dependency*: set a sensible visible range or the cursor sits at the frame edge. `values` (indicator readouts) and `data lines/labels/boxes` returned **no timestamps** (chart had only EMAs, which emit no `line/label/box.new()` objects) → **UNKNOWN, treat as UNSAFE** until re-probed with an object-emitting ICT Pine study. | ohlcv/quote/screenshot **SAFE**; pine/values **UNKNOWN→unsafe** until re-probed |

**Chart-state / version dependencies found:** replay availability depends on symbol+TF+history
depth; `values`/`pine_*` depend on which studies are loaded; screenshot framing depends on the
visible range; `horizontal_line` needs a `--time`; UI is RTL/Hebrew (cosmetic). MCP repo had an
update available at probe time (pinned to the current checkout). Report: `ict_live/devtools/tvmcp/
results/phase0_probe_20260821_191625.{md,json}`.

**Net:** the two capabilities the fidelity workflow actually needs — **(a) time+price drawing** and
**(b) causal, replay-truncated OHLCV** — are both **SAFE**. No fallback is required for overlays or
for the engine cross-check. The only "do not use in causal audits yet" items are TV's own indicator/
Pine readouts, which we don't need (the engine computes its own structure).

## 5. Proposed architecture (isolation is the design)

```
        ── PRODUCTION (unchanged, no MCP anywhere) ──────────────
        TradingView Alerts/Webhook → raw 1m store → resample → ICT engine → recommendations
        (ict_live/{market,feeds,storage,structure,...}  — never imports devtools)

        ── DEV-ONLY LAYER (new, one directory, optional) ───────
        ict_live/devtools/tvmcp/
            client.py      thin wrapper over the MCP tools (or the `tv` CLI) — the ONLY file
                           that knows MCP exists
            overlay.py     neutral EngineAnnotation objects → draw_shape calls
            audit.py       the visual_audit(symbol, date) orchestration
            probe.py       Phase-0 capability probe (answers the §4 unknowns)
        ict_live/devtools/README.md
```

**Non-negotiable rules (enforced by structure + a test):**
1. **One-way dependency.** `devtools/` may import the engine; **no engine module may import
   `devtools/`.** I'll add a unit test that fails if any non-devtools module imports `tvmcp`.
2. **Engine emits neutral data, not drawings.** The engine already produces typed objects
   (`Pool`, `Swing`, later `DealingRange`, `FVG`, `Setup`). `overlay.py` translates those into an
   `EngineAnnotation` list (kind, anchors, label, role). The engine never learns what a
   `draw_shape` is. Drawing is a pure *view* of engine state.
3. **Data source stays the raw pipeline.** For a historical audit the engine is fed **our own
   historical 1m bars** (the same source class used in the Phase-1 backtests), replayed causally
   through the *identical* resampler. TradingView is the **picture**, never the input.
   `data_get_ohlcv` is used **only** as a *data-fidelity cross-check* ("does our bar match TV's?"),
   and any mismatch is **reported**, never silently reconciled.
4. **Causality preserved.** The audit advances TV replay and the engine **in lockstep**: the engine
   is truncated at bar *k* (its normal causal prefix), TV replay is at bar *k*, overlays show only
   what the engine knew at *k*. This reuses the prefix-stability guarantee already tested.

## 6. `visual_audit <symbol> <date>` — proposed behavior

1. `tv_health_check`; if down, instruct to launch with the debug flag (no auto-trading of the app).
2. `chart_set_symbol` + `chart_set_timeframe` (per the run's analysis TF, default 1H w/ 15m + 5m as
   configured).
3. `replay_start` at `<date>`; `replay_step` bar-by-bar.
4. In lockstep, feed our historical 1m for that date into a fresh engine instance (causal prefix).
5. At each step (or at each engine event), `draw_clear` + redraw the current `EngineAnnotation`s via
   `overlay.py`; `capture_screenshot`.
6. Emit a **report** (Markdown + screenshots, optionally an Artifact) containing: the chart image,
   the engine's interpretation of all 15 primitives, the **event trail**, the recommendation, and
   **reasons for every accepted AND every rejected setup** (the event trail already records these).
7. **Fallback if §4.1 fails** (draw_shape can't anchor in time): draw all **price-level** objects as
   `horizontal_line`s (pools, CE, entry/stop/target, MSS level, manip extreme) and render the
   **time-located** objects (FVG boxes, displacement legs, sweep/AMD labels) into a **side-by-side
   Python-rendered panel** in the report instead of onto the TV chart. Fidelity is still fully
   auditable; only the on-chart convenience degrades.

## 7. Implementation order (after approval)

- **Phase 0 — `probe.py` (do first).** Answer the three §4 unknowns empirically and write the results
  into this doc. Everything downstream depends on them. No overlays until this is known.
- **Phase 1 — `client.py` + `tv_health_check`/navigation/screenshot.** Read-only: symbol/TF/date/
  replay/screenshot. Prove Claude can drive your chart.
- **Phase 2 — `overlay.py`** with whatever anchoring Phase 0 confirmed (or the §6 fallback).
- **Phase 3 — `audit.py` (`visual_audit`)** + the isolation test + a report template.

The engine work (dealing range / MSS / FVG, still gated on the `sig_swing` freeze) proceeds
independently; `visual_audit` becomes far more useful once those exist, but Phases 0–1 don't need them.

## 8. What I need from you

1. **Approve the isolation architecture** (§5) and the dev-only directory placement.
2. **Confirm** it's acceptable that this requires your **paid TradingView Desktop** launched with the
   debug port, and that it touches undocumented internals (private local dev only).
3. **Green-light Phase 0 (`probe.py`) only** — I'll report the §4 findings back before building
   overlays.
4. Note: this is **orthogonal** to the open `sig_swing` freeze from the last increment — that
   decision is still pending and unaffected.
```
