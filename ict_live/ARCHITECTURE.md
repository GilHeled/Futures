# ict_live — Architecture (v1.0)

A live intraday ICT trading **decision-support product**. It ingests market data, runs one frozen
deterministic trading engine, emits LONG/SHORT/NO-TRADE recommendations with a TRADE/PASS execution
verdict and a fixed +2R exit, tracks each trade to resolution, and reports expected-vs-actual — the
same code whether data arrives live (TradingView webhooks) or from historical replay.

> **Status: v1.0 — FROZEN.** The structural engine, execution filter, exit model, live pipeline,
> replay pipeline, and acceptance tests are frozen. This codebase is a maintained product, not a
> research playground. New trading ideas are developed **outside** the frozen pipeline and must beat
> the frozen baseline on a **new unseen dataset** before a v2 is considered (see §10).

---

## 1. Overall architecture

```
                         ┌──────────────────────────────────────────────┐
   DATA SOURCE           │              PRODUCTION PIPELINE               │        OUTPUT
                         │                (one path only)                 │
  Live:  TradingView     │                                                │
  1m webhook  ─────────► │  Ingestor ─► MarketStore (raw 1m, append-only) │
                         │      │                                          │
                         │      └─► BarBuilder ─► closed 5m/15m/1H/…       │
  Replay: historical     │                            │                   │
  bars ─► to_1m_payloads │                            ▼                   │
  ──────────────────────►│                       LiveRunner               │
                         │            (on each closed SIGNAL-TF bar)       │
                         │            │                        │          │
                         │            ▼                        ▼          │
                         │     FROZEN DECISION           TradeTracker      │
                         │     PIPELINE  ─► TradeTicket ─► open / resolve  │──► Journal (JSONL)
                         │     (engine→filter→exit)         (+2R / −1R)    │──► Report (/report.html,
                         │                                                 │     JSON, or replay stdout)
                         └──────────────────────────────────────────────┘
```

The **only** difference between live and replay is the data source on the left. Everything to the
right of the Ingestor is identical, shared code.

---

## 2. Data flow

**Live:** TradingView fires a webhook on every 1-minute bar close → `POST /webhook/tradingview` →
`LiveRunner.feed` → `Ingestor.ingest` (auth → parse → dedupe → order/gap → persist raw 1m →
`BarBuilder` resample) → any newly-closed higher-TF bars come back → on a closed **signal-TF** bar
(default 1H) the runner resolves open trades, builds a `TradeTicket`, and opens a trade if it is a
TAKE → journal + report update.

**Replay:** `replay.run` loads cached history (5m), expands each bar into aggregation-preserving
1-minute bars (`to_1m_payloads`), and feeds them through the **same** `LiveRunner.feed`. Because the
resampled timeframes and the signal-TF resolution are identical, replay reproduces exactly the
tickets, trades, and statistics the live service would have produced.

---

## 3. Main components & responsibilities

| Module | Responsibility |
|---|---|
| `market/bar.py` | The `Bar` value type (tz-aware OHLCV). |
| `market/bar_builder.py` | Resample a 1-minute stream into closed 5m/15m/1H/4H/D/W bars. |
| `market/calendar.py`, `sessions.py` | CME session-day / killzone logic (DST-safe ET). |
| `feeds/ingestor.py` | **The ingestion decision logic** (auth, parse, dedupe, ordering, gap, persist, resample). Web-server-free and unit-testable. |
| `feeds/tradingview_webhook.py` | Parse the `ict_live.bar.v1` webhook payload. |
| `storage/market_store.py` | Append-only raw-1m store; reloads on startup (restart recovery). |
| `storage/event_trail.py` | Audit log of ingestion events. |
| `engine/pipeline.py` | **FROZEN** engine: `analyze(bars, tf, …) -> MarketState` — the full ICT read (swings → liquidity → dealing range → manipulation → displacement → MSS → FVG → setup → recommendation). |
| `engine/execution_quality.py` | **FROZEN** execution filter v1 + exit model constants (`V1_WEIGHTS`, `V1_THRESHOLD`, `EXIT_MODEL`, `EXIT_TARGET_R`). |
| `structure/*.py` | The ICT detectors the engine composes (swings, liquidity, dealing_range, manipulation, displacement, mss, fvg, setup, lifecycle, ranking). **FROZEN.** |
| `live/signal.py` | `TradeTicket` + `build_ticket` — assemble engine + filter + exit into one verdict (TAKE/SKIP/NO_SETUP). |
| `live/tracker.py` | `TradeTracker` — open a trade from a TAKE, resolve to +2R/−1R/horizon, record expected-vs-actual. |
| `live/runner.py` | `LiveRunner` — the service loop: buffers, per-closed-bar orchestration, `warmup()` restart replay. |
| `live/report.py` | `build_report` / `render_html` — the monitor (open trade, last signals, closed trades, win rate, expectancy, avg R, engine health). |
| `live/serve.py` | `Config` + `build_service` + `main` — one-command run + persistence + logging. |
| `api/webhook.py` | Thin FastAPI wrapper: routes the webhook through the runner and exposes the report endpoints. |
| `replay/run.py` | Replay Runner: historical data → 1m expansion → same live pipeline; CLI. |
| `journal/` | Trade-record schema + historical measurement helpers (used by research; `record.reasoning_snapshot` is shared with the live ticket). |
| `research/` | **The closed research phase** (studies, datasets, result docs). Not on the production path. |
| `devtools/` | Dev-only visual tools (TradingView MCP). Never imported by the engine. |

---

## 4. The frozen decision pipeline

For a closed signal-TF window, `build_ticket` runs:

1. **`pipeline.analyze`** → a `MarketState`: the deterministic ICT read producing at most one current
   recommendation — direction (LONG/SHORT/NO-TRADE), **entry** = FVG consequent-encroachment,
   **stop** = manipulation extreme (−1R), and an analytical structural target. Causal, no look-ahead.
2. **`execution_quality.assess`** → TRADE/PASS: a transparent score `0.6·pd_location + 0.4·ce_distance`,
   TRADE iff `≥ 0.39`, with human-readable reasons and the weakest factor. It never changes the
   structural direction.
3. **Exit model** → the mechanical take-profit is a **full exit at +2R** (`EXIT_TARGET_R`); the
   structural liquidity target is kept as an analytical annotation only.

`action` = **TAKE** (structural direction AND execution TRADE) / **SKIP** (direction but PASS) /
**NO_SETUP**. These three layers are frozen and locked by tests.

---

## 5. Live path

`webhook → Ingestor → MarketStore + BarBuilder → LiveRunner → (TradeTicket, TradeTracker) → Journal +
Report`. Run with `python -m ict_live.live.serve` (FastAPI + uvicorn). On each closed signal-TF bar
the runner (a) resolves open trades against that bar, (b) builds the ticket, (c) opens a TAKE. State
is reconstructable from the append-only 1m store, so a restart loses nothing (`warmup()` replays it).
Endpoints: `POST /webhook/tradingview`, `GET /report[.html]`, `/signals`, `/trades`, `/status`,
`/health`.

## 6. Replay path

`historical bars → to_1m_payloads → [the entire live path] → Report`. Run with
`python -m ict_live.replay.run --symbol MES --from 2025-01-01 --to 2025-03-31 [--out report.html]`.
Same components, same results — there is no separate backtester. Any future historical study runs
through this, not through a parallel implementation.

---

## 7. Configuration

Live service (env vars, all optional; local defaults): `ICT_LIVE_TOKEN` (webhook auth),
`ICT_LIVE_DATA_DIR` (persisted state), `ICT_LIVE_HOST`/`PORT`, `ICT_LIVE_SIGNAL_TF` (default `1H`),
`ICT_LIVE_ENTRY_TF` (default `15m`). Frozen trading constants live in code, not env:
`engine/execution_quality.py` (filter weights/threshold, exit model) and `config.py`
(`MIN_RR`, `MIN_STOP_TICKS`, `INSTRUMENTS`). Replay takes the same timeframes as CLI flags.

## 8. Persistence & restart

Raw 1m → `<data_dir>/raw_1m.jsonl` (append-only, reloaded on startup). Signals and closed trades →
`<data_dir>/signals/`. Open trades and all timeframe buffers are rebuilt deterministically by
replaying the stored 1m (`LiveRunner.warmup`) — the store is the source of truth.

## 9. Runtime environments

Two entrypoints, **one** codebase. The **live service** runs under the FastAPI/uvicorn environment
(system `python3`); the **replay runner** runs under the research venv (`.venv`, which has pandas to
load history). The trading pipeline itself has no heavyweight dependencies and behaves identically in
both.

---

## 10. Where production ends and research begins

**Production** = everything on the pipeline in §1: `market/`, `feeds/`, `storage/`, `engine/`,
`structure/`, `live/`, `replay/`, `api/`. It is frozen at v1.0.

**Research** = `research/` (and `journal/` measurement helpers, `devtools/`). The engine never imports
`devtools/` (enforced by `tests/test_devtools_isolation.py`), and no production module imports
`research/` — the single exception is the replay runner's **lazy** historical-data loader
(`replay/run.py` imports `research.data` only to read cached files; it is the data-source adapter, not
trading logic). The research phase that produced v1.0 is **closed** and retained only as the record
(studies + result docs + the hand-labeled fidelity dataset).

**New trading ideas do not go here.** The workflow for any future idea:

1. Write the hypothesis.
2. Define the acceptance criteria up front.
3. Test on a **completely new unseen dataset** (or forward live data) — never the spent OOS.
4. Only if it **clearly beats the frozen v1.0 baseline** do we build a v2, as a separate, isolated
   line that graduates into production only after it proves itself.

The three kinds of work allowed in this repo: **(1) bug fixes, (2) operational improvements**
(performance, logging, deployment, monitoring, reliability), **(3) isolated new research** kept out
of the production engine until proven.

## 11. Testing / acceptance

`tests/test_acceptance_e2e.py` is the permanent end-to-end regression: it drives the full lifecycle
over the real FastAPI webhook (1m in → +2R close → persisted → report). The frozen layers are pinned
by `tests/test_execution_quality.py::{test_v1_is_frozen, test_exit_model_is_frozen}`. Any change that
alters a frozen trading decision breaks these tests by design.

## 12. Frozen v1.0 inventory

- **Structural engine** — `engine/pipeline.py` + `structure/*`.
- **Execution filter** — `execution_quality.V1_WEIGHTS = {pd_location:0.6, ce_distance:0.4, …}`, `V1_THRESHOLD = 0.39`.
- **Exit model** — `execution_quality.EXIT_MODEL = "fixed_2R"`, `EXIT_TARGET_R = 2.0`.
- **Live pipeline** — `feeds/`, `storage/`, `market/`, `live/`, `api/`.
- **Replay pipeline** — `replay/run.py`.
- **Acceptance tests** — `tests/test_acceptance_e2e.py` (+ the freeze-lock tests).
