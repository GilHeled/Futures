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

## Run with Docker (one command)
The live service + dashboard run in Docker from a single image. The **data feed** is the one piece
that can't always live in the container — the real-time TradingView feed depends on TradingView
Desktop (a GUI app that cannot run in a Linux container) — so there are two modes:

**Real-time (recommended) — one command, `./run-live.sh`:**
```bash
./run-live.sh                 # or: ./run-live.sh CME_MINI:MES1!
```
This builds + starts the live service and dashboard in Docker, waits for health, then runs the
real-time TradingView **MCP feed on the host** (TradingView Desktop must be open with
`--remote-debugging-port=9222` on that symbol; `TV_CLI` set). `Ctrl+C` stops the feed and tears the
stack down. Near-zero data delay.

**Fully in Docker (delayed, no TradingView) — for evaluation on any machine:**
```bash
docker compose -f docker-compose.ict_live.yml --profile sim up --build
```
Adds the in-container **yfinance** feed (~10–15 min delayed). Without `--profile sim`, plain
`docker compose … up` starts just the live service + dashboard (attach your own feed).

Either way: dashboard at **http://127.0.0.1:8010**, live monitor at **http://127.0.0.1:8000/report.html**.
Trade state persists in the `ict_live_data` volume; the historical cache is bind-mounted read-only for
REPLAY. Set `ICT_LIVE_TOKEN` to require auth (add `--token` to whichever feed you run). This compose
file is separate from the repo-root one (which builds `mnq_system`).

## Run manually (three terminals)

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

## Feeding the live service (making LIVE populate)
The service only shows signals once it is *receiving* 1-minute bars. Two ways to feed it:

**A. TradingView webhook (authoritative / production).** A TradingView alert, fired on each 1-minute
bar close, POSTs the `ict_live.bar.v1` payload to `…/webhook/tradingview`. Full setup:

1. **Plan:** TradingView Pro / Pro+ / Premium (webhook alerts are not on the free plan).
2. **Run the service with a token:**
   ```bash
   ICT_LIVE_TOKEN=YOUR_SECRET python3 -m ict_live.live.serve
   ```
3. **Expose it publicly** (TradingView's servers can't reach `localhost`):
   ```bash
   cloudflared tunnel --url http://localhost:8000      # or:  ngrok http 8000
   ```
   Note the printed `https://…` host. Your **webhook URL** is:
   `https://<that-host>/webhook/tradingview?token=YOUR_SECRET`
   (TradingView can't send custom headers, so the token goes in the query string; the service
   accepts it there.)
4. **Add the Pine feed:** open a **1-minute** chart of a known symbol (`CME_MINI:MNQ1!`,
   `CME_MINI:MES1!`, `CME_MINI:NQ1!`, or `CME_MINI:ES1!`); Pine Editor → paste
   `tradingview_alert.pine` → **Add to chart**.
5. **Create the alert:** Condition = "ict_live 1m webhook feed" / **Any alert() function call**;
   frequency **Once Per Bar Close**; Notifications → **Webhook URL** = the URL from step 3. (The alert
   message is supplied by the script's `alert()` call, so the dialog's message box is ignored.)
6. **Repeat per symbol** (one chart + one alert each).
7. **Verify:** `GET /report.html` shows accepted bars / signals as they arrive.

Sanity-check the endpoint locally before wiring TradingView:
```bash
curl -X POST "http://127.0.0.1:8000/webhook/tradingview?token=YOUR_SECRET" \
  -H 'Content-Type: application/json' \
  -d '{"schema":"ict_live.bar.v1","symbol":"CME_MINI:MNQ1!","resolution":"1","bar_time_ms":1705329000000,"bar_close_ms":1705329060000,"open":17000,"high":17005,"low":16998,"close":17002,"volume":123}'
# -> {"status":"accepted", ...}
```
This is the frozen, authoritative feed. The service + tunnel must stay running.

**B. TradingView Desktop via MCP (real-time, local — no Pro webhook / tunnel).** Reuses the dev
TradingView MCP connection as a live feed: reads your chart's real-time 1m bars and POSTs the
closed ones to the webhook. This is **near-zero delay** (your real TradingView data; ~1 min = one
closed bar), unlike yfinance's ~10–15 min lag. Requires TradingView Desktop running with
`--remote-debugging-port` and the `tv` CLI:
```bash
TV_CLI="node ~/dev/tradingview-mcp/src/cli/index.js" \
  python -m ict_live.devtools.tvmcp.live_feed --url http://127.0.0.1:8000 --symbol CME_MINI:MNQ1!
```
Notes: **one symbol per chart** (the MCP targets one chart — run one per symbol/chart); it switches
the chart to the **1-minute** timeframe (dedicate a chart to it); host-side only (can't run inside
the container — point it at the service's `:8000`). The charted symbol must be a known instrument
(e.g. `CME_MINI:MNQ1!`). It's a devtools-tier producer of the same webhook payload — no trading logic.

**C. yfinance feed bridge (convenience / evaluation).** Pull recent 1m bars from yfinance and POST
them — no TradingView at all:
```bash
.venv/bin/python -m ict_live.live.feed_bridge --url http://127.0.0.1:8000 --symbols MES MNQ
```
Backfills on start then streams each minute. **Non-authoritative and ~10–15 min delayed** (yfinance
is a continuous-contract proxy) — fine for watching the system, not for precise entries. Only
`config.INSTRUMENTS` symbols (MES/MNQ/ES/NQ).

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

### Operational dashboard (browser front-end for LIVE + REPLAY)
```bash
.venv/bin/python -m ict_live.replay.dashboard
```
LIVE proxies `http://127.0.0.1:8000` by default; override with `--live-url` and `--port`:
```bash
.venv/bin/python -m ict_live.replay.dashboard --live-url http://127.0.0.1:8000 --port 8010
```
It prints `ict_live dashboard: http://127.0.0.1:PORT …` — open that URL (auto-picks a free port).
One page, two areas, both driven by the **existing** frozen system — the dashboard holds no trading
logic and no second implementation of anything:

- **LIVE** — a read-only, server-side proxy of the running live service's existing `/report` (set
  `--live-url`): live signals incl. SKIP / NO_SETUP with entry / stop / +2R / structural target /
  execution score / weakest factor / reasoning snapshot, open trades, closed trades (result R,
  MFE/MAE), running stats (trades / win rate / expectancy / total R / PF / max DD), and engine health
  / last processed bar. Refreshes every few seconds; shows "not connected" if the live service is
  down.
- **REPLAY** — drives `replay.run.replay` unchanged: pick symbol(s) + date range + aggregation, run,
  watch progress, see per-symbol stats side by side, download each trade CSV.

Runs under the research venv (pandas, for replay); stdlib HTTP — no web framework. The live service
itself runs separately under its FastAPI env (`python -m ict_live.live.serve`); the dashboard only
reads its API.
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
