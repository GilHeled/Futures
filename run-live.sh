#!/usr/bin/env bash
# One command for the full live stack. Data source defaults to REAL-TIME TradingView (via the MCP
# connection to TradingView Desktop on this Mac); it falls back to the delayed yfinance feed only if
# TradingView isn't reachable. The live service + dashboard run in Docker; the feed runs on the host
# (TradingView Desktop is a GUI app that can't run in a container).
#
# Usage:   ./run-live.sh [SYMBOL ...]    (default: CME_MINI:MNQ1! CME_MINI:MES1!)
# Ctrl+C stops the feed and tears the Docker stack down.
#
# On start it WARMS UP the engine's history with a one-time yfinance backfill (WARMUP_DAYS, default 7)
# so signals can form immediately, then switches to the real-time source. Set WARMUP_DAYS=0 to skip.
#
# For real-time TradingView, launch TradingView Desktop with the debug port once:
#   open -a TradingView --args --remote-debugging-port=9222
set -euo pipefail
cd "$(dirname "$0")"

# Default set includes the major commodities alongside the index micros. Pass your own to override,
# e.g.  ./run-live.sh CME_MINI:MNQ1! COMEX:GC1!
if [ "$#" -gt 0 ]; then
  SYMBOLS=("$@")
else
  SYMBOLS=(CME_MINI:MNQ1! CME_MINI:MES1! COMEX:GC1! NYMEX:CL1! NYMEX:NG1! COMEX:SI1!)
fi
COMPOSE="docker compose -f docker-compose.ict_live.yml"
export TV_CLI="${TV_CLI:-node $HOME/dev/tradingview-mcp/src/cli/index.js}"
PY="${PY:-.venv/bin/python}"; [ -x "$PY" ] || PY="python3"
WARMUP_DAYS="${WARMUP_DAYS:-7}"

# Reset the persisted store so the historical warm-up seeds cleanly (the ingestor only accepts
# forward bars, so a backfill can't load behind stale data left from a previous run). The warm-up
# re-seeds the full window each start. Set KEEP_STORE=1 to preserve it (and skip the reset).
if [ "${KEEP_STORE:-0}" != "1" ]; then
  echo "==> resetting persisted store for a clean warm-up (KEEP_STORE=1 to preserve)…"
  $COMPOSE down -v >/dev/null 2>&1 || true
fi

echo "==> building + starting live service and dashboard (Docker)…"
$COMPOSE up -d --build live dashboard

echo "==> waiting for the live service to be healthy…"
until curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 2; done
echo "    dashboard: http://127.0.0.1:8010   ·   monitor: http://127.0.0.1:8000/report.html"

cleanup() { echo; echo "==> stopping feed and tearing down Docker…"; $COMPOSE down; }
trap cleanup EXIT INT TERM

# Mark the seed→live boundary at NOW: everything backfilled below only PRIMES structure and can
# never surface as a tradable ticket; only real-time bars after this instant open trades.
echo "==> marking live boundary (warm-up seeds structure only; live bars trade)…"
curl -sf -X POST http://127.0.0.1:8000/live/mark-now -H 'Content-Type: application/json' -d '{}' \
  >/dev/null || echo "   (could not mark live boundary — continuing)"

# One-time WARM-UP: backfill history from yfinance so the engine has enough 1H bars to form setups
# (the real-time MCP feed only seeds ~100 bars). Historical bars aren't delayed, so this is safe.
# These bars are pre-boundary, so they build the structural window WITHOUT opening any trades.
if [ "$WARMUP_DAYS" != "0" ]; then
  echo "==> warming up ~${WARMUP_DAYS}d of history (yfinance backfill; one-time)…"
  "$PY" -m ict_live.live.feed_bridge --url http://127.0.0.1:8000 --symbols "${SYMBOLS[@]}" \
    --backfill "${WARMUP_DAYS}d" --once || echo "   (warm-up skipped/failed — continuing)"
fi

# Then stream live. Prefer REAL-TIME TradingView (MCP); fall back to yfinance if TV isn't reachable.
if "$PY" -c "from ict_live.devtools.tvmcp.client import TvClient; import sys; sys.exit(0 if TvClient().available() else 1)" 2>/dev/null; then
  echo "==> DATA SOURCE: TradingView Desktop (MCP, real-time)  ·  ${SYMBOLS[*]}  (Ctrl+C to stop)"
  exec "$PY" -m ict_live.devtools.tvmcp.live_feed --url http://127.0.0.1:8000 --symbols "${SYMBOLS[@]}"
else
  echo "!! TradingView MCP not reachable — falling back to yfinance (DELAYED ~10-15 min)."
  echo "   For real-time, launch: open -a TradingView --args --remote-debugging-port=9222"
  exec "$PY" -m ict_live.live.feed_bridge --url http://127.0.0.1:8000 --symbols "${SYMBOLS[@]}"
fi
