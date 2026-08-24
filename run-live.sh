#!/usr/bin/env bash
# One command for the full live stack. Data source defaults to REAL-TIME TradingView (via the MCP
# connection to TradingView Desktop on this Mac); it falls back to the delayed yfinance feed only if
# TradingView isn't reachable. The live service + dashboard run in Docker; the feed runs on the host
# (TradingView Desktop is a GUI app that can't run in a container).
#
# Usage:   ./run-live.sh [SYMBOL ...]    (default: CME_MINI:MNQ1! CME_MINI:MES1!)
# Ctrl+C stops the feed and tears the Docker stack down.
#
# For real-time TradingView, launch TradingView Desktop with the debug port once:
#   open -a TradingView --args --remote-debugging-port=9222
set -euo pipefail
cd "$(dirname "$0")"

if [ "$#" -gt 0 ]; then SYMBOLS=("$@"); else SYMBOLS=(CME_MINI:MNQ1! CME_MINI:MES1!); fi
COMPOSE="docker compose -f docker-compose.ict_live.yml"
export TV_CLI="${TV_CLI:-node $HOME/dev/tradingview-mcp/src/cli/index.js}"
PY="${PY:-.venv/bin/python}"; [ -x "$PY" ] || PY="python3"

echo "==> building + starting live service and dashboard (Docker)…"
$COMPOSE up -d --build live dashboard

echo "==> waiting for the live service to be healthy…"
until curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 2; done
echo "    dashboard: http://127.0.0.1:8010   ·   monitor: http://127.0.0.1:8000/report.html"

cleanup() { echo; echo "==> stopping feed and tearing down Docker…"; $COMPOSE down; }
trap cleanup EXIT INT TERM

# Prefer REAL-TIME TradingView (MCP); fall back to yfinance only if TradingView isn't reachable.
if "$PY" -c "from ict_live.devtools.tvmcp.client import TvClient; import sys; sys.exit(0 if TvClient().available() else 1)" 2>/dev/null; then
  echo "==> DATA SOURCE: TradingView Desktop (MCP, real-time)  ·  ${SYMBOLS[*]}  (Ctrl+C to stop)"
  exec "$PY" -m ict_live.devtools.tvmcp.live_feed --url http://127.0.0.1:8000 --symbols "${SYMBOLS[@]}"
else
  echo "!! TradingView MCP not reachable — falling back to yfinance (DELAYED ~10-15 min)."
  echo "   For real-time, launch: open -a TradingView --args --remote-debugging-port=9222"
  exec "$PY" -m ict_live.live.feed_bridge --url http://127.0.0.1:8000 --symbols "${SYMBOLS[@]}"
fi
