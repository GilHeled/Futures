"""Real-time live feed from TradingView Desktop via the MCP `tv` CLI.

Same TradingView connection used for dev (chart reads), repurposed to push REAL-TIME 1-minute bars to
the frozen live service — so the live signals run on your real TradingView data with ~no delay,
instead of the ~10-15 min yfinance proxy, and without a TradingView-Pro webhook or a public tunnel.

It reads the current chart's 1-minute OHLCV (`tv ohlcv`), skips the still-forming bar, and POSTs each
newly-closed bar to `/webhook/tradingview` as an `ict_live.bar.v1` payload — exactly what a
TradingView alert would send. It adds NO trading logic; it is a devtools-tier producer of the same
webhook payload (the engine never imports devtools — this lives on the dev side of that line).

LIMITATIONS (be aware):
  * ONE symbol per run — the MCP targets a single TradingView chart. Run one per chart/symbol.
  * It sets the chart to the 1-minute timeframe (dedicate a chart to the feed).
  * Requires TradingView Desktop running with --remote-debugging-port and the `tv` CLI
    (TV_CLI, e.g. "node ~/dev/tradingview-mcp/src/cli/index.js"). Host-side only.
  * The charted symbol must be one the service knows (config.INSTRUMENTS), e.g. CME_MINI:MNQ1!.

  TV_CLI="node ~/dev/tradingview-mcp/src/cli/index.js" \
    python -m ict_live.devtools.tvmcp.live_feed --url http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import time

from ict_live import config as C
from ict_live.devtools.tvmcp.client import TvClient
from ict_live.live.feed_bridge import _post, _reachable   # reuse POST + preflight (allowed direction)

SCHEMA = "ict_live.bar.v1"


def chart_symbol(tv: TvClient) -> str | None:
    st = tv.status()
    return (st.data or {}).get("chart_symbol") if st.ok else None


def closed_bars(tv: TvClient):
    """Return the chart's 1m bars that are fully CLOSED (drops the forming last bar)."""
    r = tv.ohlcv()
    if not r.ok or not isinstance(r.data, dict):
        return None
    bars = r.data.get("bars") or []
    now = time.time()
    return [b for b in bars if (b.get("time", 0) + 60) <= now]      # closed = open+60s in the past


def push_new(tv, url, symbol, *, token, last_ms: dict, log=print) -> int:
    bars = closed_bars(tv)
    if bars is None:
        log("ohlcv read failed (is the chart on 1m and TradingView reachable?)")
        return 0
    posted = 0
    for b in bars:
        open_ms = int(b["time"]) * 1000
        if open_ms <= last_ms.get(symbol, -1):
            continue
        payload = {"schema": SCHEMA, "symbol": symbol, "resolution": "1",
                   "bar_time_ms": open_ms, "bar_close_ms": open_ms + 60_000,
                   "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                   "close": float(b["close"]), "volume": float(b.get("volume", 0) or 0)}
        if _post(url, payload, token).get("status") == "accepted":
            last_ms[symbol] = open_ms
            posted += 1
    return posted


def run(url, *, symbol=None, token=None, interval=15, once=False, tv_binary=None, tv_cwd=None,
        log=print) -> dict:
    tv = TvClient(binary=tv_binary, cwd=tv_cwd)
    if not tv.available():
        raise SystemExit("TradingView MCP `tv` CLI not reachable — set TV_CLI and ensure TradingView "
                         "Desktop is running with --remote-debugging-port.")
    if not _reachable(url):
        raise SystemExit(f"live service not reachable at {url} — start it first: "
                         f"python -m ict_live.live.serve")
    if symbol:
        tv.set_symbol(symbol)
    sym = symbol or chart_symbol(tv)
    if sym not in C.INSTRUMENTS:
        raise SystemExit(f"chart symbol {sym!r} is not a known instrument {sorted(C.INSTRUMENTS)} — "
                         f"chart one of them (e.g. CME_MINI:MNQ1!)")
    tv.set_timeframe("1")                                          # feed needs 1-minute bars
    log(f"TradingView live feed: {sym} -> {url}  (real-time via MCP)")
    last_ms: dict[str, int] = {}
    n = push_new(tv, url, sym, token=token, last_ms=last_ms, log=log)
    log(f"seeded {n} closed bars")
    if once:
        return {"symbol": sym, "posted": n}
    while True:
        time.sleep(interval)
        try:
            k = push_new(tv, url, sym, token=token, last_ms=last_ms, log=log)
            if k:
                log(f"{sym}: +{k} bars")
        except Exception as e:
            log(f"poll error {type(e).__name__}: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Real-time TradingView (MCP) feed into the live webhook.")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--symbol", default=None, help="chart this symbol (default: use current chart)")
    ap.add_argument("--token", default=None)
    ap.add_argument("--interval", type=int, default=15, help="poll seconds")
    ap.add_argument("--once", action="store_true")
    ns = ap.parse_args()
    run(ns.url, symbol=ns.symbol, token=ns.token, interval=ns.interval, once=ns.once)


if __name__ == "__main__":
    main()
