# ict_live — live MVP service

Turns the **frozen** research system into a stable advisory service that continuously produces trade
recommendations and measures them against what actually happens. It places no orders, sizes no
positions, and sends no notifications — it records and reports.

## What it does
On every closed **signal-TF** bar (default 1H) for each symbol:
1. resolve any open trade against that bar using the frozen exit (full exit at **+2R**, stop = −1R);
2. run the **frozen** engine + execution-filter v1 to build a `TradeTicket`
   (`action` = TAKE / SKIP / NO_SETUP, structural direction, entry/stop, +2R exit, reasons);
3. if the ticket is a **TAKE**, open a tracked trade.

Everything else is ignored.

## Frozen layers (do not change without a new hypothesis + a new unseen dataset)
- **Engine** — ICT structure → LONG/SHORT, entry = FVG CE, stop = manipulation extreme (−1R).
- **Execution filter v1** — `0.6·pd_location + 0.4·ce_distance ≥ 0.39` → TRADE/PASS.
- **Exit model** — full exit at **+2R** (`EXIT_MODEL="fixed_2R"`), OOS-confirmed.

These are locked by tests (`test_execution_quality.py::test_v1_is_frozen` /
`::test_exit_model_is_frozen`).

## Run

```bash
python -m ict_live.live.serve
```

Environment (all optional):

| var | default | meaning |
|---|---|---|
| `ICT_LIVE_TOKEN` | (unset) | bearer token required on the webhook; unset = auth off (local only) |
| `ICT_LIVE_DATA_DIR` | `./ict_live_data` | persisted state (raw 1m + signal/trade logs) |
| `ICT_LIVE_HOST` / `ICT_LIVE_PORT` | `127.0.0.1` / `8000` | bind address |
| `ICT_LIVE_SIGNAL_TF` / `ICT_LIVE_ENTRY_TF` | `1H` / `15m` | timeframes |

Point a TradingView 1m-bar-close alert at `POST /webhook/tradingview` (payload `ict_live.bar.v1`).

## Endpoints
- `POST /webhook/tradingview` — ingest one 1m bar; runs the pipeline on a closed signal-TF bar.
- `GET  /report.html` — the monitor page (open trade, last 20 signals, closed trades, win rate,
  expectancy, avg R, engine health / last processed bar).
- `GET  /report`, `/signals`, `/trades` — the same data as JSON.
- `GET  /status`, `/health` — ingestion counts and liveness.

## Reliability
- **Restart-safe.** Raw 1m is appended to `raw_1m.jsonl` and reloaded on startup; `warmup()` replays
  it through the bar builders to rebuild all timeframe buffers and open/closed trade state — a
  restart loses nothing. Signals and closed trades are appended to `signals/` as they occur.
- **Deterministic.** Live resolution mirrors the backtest exactly (fill = first bar containing the
  entry; same intrabar-ambiguity rule), so the live track record is directly comparable to the
  dev/OOS studies.

## One engine, two data sources
The same pipeline runs historical data through **replay** — only the data source differs (cached
history instead of live webhooks). Each historical bar is expanded into aggregation-preserving 1m
bars and fed through the identical Ingestor → BarBuilder → LiveRunner → frozen engine/filter/exit →
TradeTracker → journal, so replay produces exactly the tickets, trades, and stats the live service
would have. There is no separate backtester.

```bash
.venv/bin/python -m ict_live.replay.run --symbol MES --from 2025-01-01 --to 2025-03-31
.venv/bin/python -m ict_live.replay.run --symbol MNQ --from 2026-01-01 --to 2026-06-30 --out report.html
```
Options: `--period {none,month,quarter}` prints an OVERALL + per-period breakdown (win rate,
expectancy, profit factor, max drawdown R, win/loss streaks, hold times); `--export-trades FILE.csv`
writes **every completed trade** (times, prices, R, win, bars held, MFE/MAE, execution score, weakest
factor, and the reasoning snapshot) so any analysis can be done externally without changing the
system. These are reporting outputs; they never affect the frozen trading decisions.

### Replay dashboard (browser front-end)
```bash
.venv/bin/python -m ict_live.replay.dashboard      # open the printed http://127.0.0.1:PORT
```
A browser UI over the **same** replay runner: pick one or more symbols and a date range, choose the
aggregation, start the run, watch progress, see per-symbol summary stats side by side, and download
each trade CSV. It is a front-end only — it drives `replay.run.replay` unchanged and adds no trading
logic (the progress readout is a no-op instrumentation hook). Runs under the research venv (pandas),
stdlib HTTP — no web framework.
(Replay runs under the research venv, which has pandas to load history; the live service runs under
the FastAPI env. Both execute the same trading code.)

## Layout
```
live/
  signal.py   TradeTicket + build_ticket   (frozen engine + exec v1 + 2R exit → one verdict)
  tracker.py  TradeTracker                 (open → resolve to +2R/−1R, expected vs actual)
  runner.py   LiveRunner                   (webhook → buffers → ticket → track; warmup replay)
  report.py   build_report / render_html   (the monitor)
  serve.py    Config + build_service + main (one-command run)
replay/
  run.py      to_1m_payloads + replay + CLI (historical data → same live pipeline)
```
The full lifecycle (webhook → timeframes → setup → ticket → open → +2R close → persist → report) is
locked by `tests/test_acceptance_e2e.py`.

## Scope (intentionally small)
No notifications, no broker integration, no order execution, no position sizing, no portfolio
management, no dashboards, no optimization, no new research. The point is to run it every day in
parallel with discretionary trading and accumulate a live, measured track record.
