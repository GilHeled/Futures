#!/usr/bin/env bash
# One command for the FULL real-time live stack:
#   - live service + dashboard run in Docker (docker-compose.ict_live.yml)
#   - the real-time TradingView feed runs on the HOST (TradingView Desktop is a GUI app that
#     cannot run in a container), streaming your chart's 1m bars into the dockerised service.
#
# Usage:   ./run-live.sh [SYMBOL ...]    (default: CME_MINI:MNQ1! CME_MINI:MES1!)
# Ctrl+C stops the feed and tears the Docker stack down. Multiple symbols are streamed round-robin
# through the single TradingView chart (it flips between them); toggle them on/off in the dashboard.
#
# Prereqs on the host: docker, python3, node + ~/dev/tradingview-mcp, and TradingView Desktop
# running with --remote-debugging-port=9222.
set -euo pipefail
cd "$(dirname "$0")"

if [ "$#" -gt 0 ]; then SYMBOLS=("$@"); else SYMBOLS=(CME_MINI:MNQ1! CME_MINI:MES1!); fi
COMPOSE="docker compose -f docker-compose.ict_live.yml"
export TV_CLI="${TV_CLI:-node $HOME/dev/tradingview-mcp/src/cli/index.js}"

echo "==> building + starting live service and dashboard (Docker)…"
$COMPOSE up -d --build live dashboard

echo "==> waiting for the live service to be healthy…"
until curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 2; done
echo "    live service ready  ·  dashboard: http://127.0.0.1:8010  ·  monitor: http://127.0.0.1:8000/report.html"

cleanup() { echo; echo "==> stopping feed and tearing down Docker…"; $COMPOSE down; }
trap cleanup EXIT INT TERM

echo "==> starting REAL-TIME TradingView feed on host for ${SYMBOLS[*]} (Ctrl+C to stop everything)…"
exec python3 -m ict_live.devtools.tvmcp.live_feed --url http://127.0.0.1:8000 --symbols "${SYMBOLS[@]}"
