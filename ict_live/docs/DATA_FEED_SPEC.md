# DATA_FEED_SPEC — ict_live (TradingView → Python, transport only)

> The feed is a **dumb transport**: Pine sends completed 1-minute OHLCV bars; Python does
> all timeframe construction and all methodology. This keeps the strategy in one testable
> place and lets us swap TradingView for a broker/data-provider API later without touching
> the engine. **No unofficial TradingView websocket scraper** — official alert/webhook only.

## 1. Pine Script (transport layer, NOT the strategy)
Purpose: fire one webhook per **completed** 1-minute bar. It must not compute any strategy
logic and must not fire on the live/forming bar.

```pine
//@version=5
indicator("ict_live 1m transport", overlay=true)
// Fire only on a confirmed (closed) bar to guarantee causal, non-repainting data.
bar_closed = barstate.isconfirmed
alertMessage = '{' +
  '"schema":"ict_live.bar.v1",' +
  '"symbol":"' + syminfo.tickerid + '",' +          // e.g. CME_MINI:NQ1!
  '"resolution":"' + timeframe.period + '",' +       // must be "1"
  '"bar_time_ms":' + str.tostring(time) + ',' +      // bar OPEN time, exchange epoch ms
  '"bar_close_ms":' + str.tostring(time_close) + ',' +
  '"open":' + str.tostring(open) + ',' +
  '"high":' + str.tostring(high) + ',' +
  '"low":' + str.tostring(low) + ',' +
  '"close":' + str.tostring(close) + ',' +
  '"volume":' + str.tostring(volume) + '}'
if bar_closed
    alert(alertMessage, alert.freq_once_per_bar_close)
```
- Attach on a **1-minute** chart of the target CME symbol; create an alert with
  "Any alert() function call", condition = this indicator, and the webhook URL.
- `alert.freq_once_per_bar_close` + `barstate.isconfirmed` ⇒ exactly one non-repainting
  message per closed 1m bar.
- Chart timezone is irrelevant: `time`/`time_close` are epoch ms; Python converts to ET.
- One alert per symbol; the payload's `symbol` disambiguates streams.

## 2. Webhook payload (contract `ict_live.bar.v1`)
```json
{
  "schema": "ict_live.bar.v1",
  "symbol": "CME_MINI:NQ1!",
  "resolution": "1",
  "bar_time_ms": 1730472000000,
  "bar_close_ms": 1730472060000,
  "open": 20345.25, "high": 20351.00, "low": 20344.50,
  "close": 20349.75, "volume": 1234
}
```
Optional/settable via alert config: a shared `token` (auth), `contract_month` if front
rolls matter. Endpoint: `POST /webhook/tradingview`. Auth: static bearer/`token` check
(Phase 1); reject unknown tokens.

## 3. Ingestion validation (api/webhook.py → market_store)
For each message, in order, with the outcome logged to the event trail:
1. **Schema/auth**: known `schema`, valid `token`, `resolution=="1"`, numeric OHLCV,
   `high>=max(open,close)`, `low<=min(open,close)`, `high>=low`. Reject + log otherwise.
2. **Symbol routing**: map `symbol` → internal instrument (contract specs, tick size,
   session profile). Unknown symbol → log + ignore.
3. **Dedupe**: `(symbol, bar_time_ms)` seen before → drop (idempotent; TradingView can
   resend). If OHLCV differs from the stored bar for the same key → log a **conflict** and
   keep the first (never silently overwrite closed data).
4. **Ordering**: `bar_time_ms` must be strictly increasing per symbol. Out-of-order (older
   than last stored) → log, store in place, and mark the affected downstream TF bars for
   recompute (do not append as newest).
5. **Gap detection**: expected next open = last_close + 60s during an active session (per
   `market/calendar.py`, which knows CME hours/holidays/half-days). A gap → log
   `missing_bars(n)`; higher-TF bars spanning the gap are flagged `incomplete=true` and are
   **not** treated as closed.
6. **Persist raw**: append to `storage/market_store` (append-only, per-symbol per-day
   Parquet + a hot in-memory ring for the current sessions).

## 4. Deterministic resampling (market/bar_builder.py)
- Build 5m, 15m, 1H, 4H, Daily, Weekly from stored 1m, **ET-boundary aligned**, using the
  same left-closed convention as `mnq_system.timeframe_alignment` / `ict_faithful._resample`.
- A higher-TF bar is **emitted as CLOSED only when its final constituent 1m bar has closed**
  (e.g. the 5m bar for 09:30–09:35 closes when the 09:34 1m bar arrives). Until then the
  engine may read it only as a **forming** bar, explicitly labelled `forming=true`, and no
  confirmed signal (swing/FVG/session-extreme) may depend on a forming bar.
- Session boundaries (Daily = CME session, Weekly = Sun-open→Fri-close) come from
  `market/calendar.py` (ET, DST-aware); daily/weekly levels are **developing** until the
  session closes.
- Resampling is a **pure function** of the stored 1m series (no state, no wall-clock) →
  fully reproducible and unit-testable; a replay of the same 1m stream yields identical
  higher-TF bars.

## 5. Causality guarantees (tested)
- Engine tick at time *t* sees: all 1m bars with `bar_close_ms <= t`; higher-TF bars only
  where fully closed by *t* (else `forming`). Nothing dated after *t* is visible.
- Swings need their confirmation-width future bars closed before "confirmed"; FVG needs its
  3rd candle closed; session H/L is "developing" until session close. All labelled.
- **Prefix-stability test**: replaying the 1m stream truncated at any *t* must reproduce the
  exact engine state/recommendation that the full stream produced at *t*. This is a
  mandatory test, not an afterthought (the overnight-line lesson).

## 6. Reliability / operations (Phase 1)
- **Heartbeat/liveness**: `/health` reports last-bar age per symbol; a stale feed (no 1m bar
  for > N minutes during a session) raises a flag and suppresses fresh ACTIONABLE
  recommendations (stale data must not produce a trade signal).
- **Replay/backfill**: the same webhook payloads can be replayed from stored 1m (or a
  historical daily/intraday pull) through the identical pipeline — this is how the frozen
  engine is later backtested, guaranteeing live == backtest logic.
- **Idempotent + append-only** storage ⇒ safe restart: on boot, rebuild engine state by
  replaying the day's stored 1m bars.
