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
# reuse POST + preflight + control/heartbeat (devtools→live is the allowed import direction)
from ict_live.live.feed_bridge import (_post, _reachable, get_enabled, get_last_closes, heartbeat,
                                        post_chart)

SCHEMA = "ict_live.bar.v1"
SOURCE = "TradingView Desktop (MCP, real-time)"
DEFAULT_SYMBOLS = ["CME_MINI:MNQ1!", "CME_MINI:MES1!"]


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


MAX_PRICE_DEVIATION = 0.25            # reject a bar whose price is >25% off the symbol's recent close


def push_new(tv, url, symbol, *, token, last_ms: dict, last_close: dict = None, log=print) -> int:
    bars = closed_bars(tv)
    if bars is None:
        log("ohlcv read failed (is the chart on 1m and TradingView reachable?)")
        return 0
    posted = 0
    for b in bars:
        open_ms = int(b["time"]) * 1000
        if open_ms <= last_ms.get(symbol, -1):
            continue
        c = float(b["close"])
        # Plausibility guard: the MCP round-robins ONE chart, and a symbol switch can briefly return
        # the PREVIOUS symbol's bars (e.g. a MES ~7675 bar under MNQ ~29000). Reject any bar wildly
        # off this symbol's recent close so a wrong-symbol/bad bar can't poison the engine's structure.
        ref = (last_close or {}).get(symbol)
        if ref and abs(c - ref) / ref > MAX_PRICE_DEVIATION:
            log(f"{symbol}: SKIP implausible bar close={c:g} vs recent {ref:g} "
                f"(wrong-symbol/bad tick?) — not posted")
            continue
        payload = {"schema": SCHEMA, "symbol": symbol, "resolution": "1",
                   "bar_time_ms": open_ms, "bar_close_ms": open_ms + 60_000,
                   "open": float(b["open"]), "high": float(b["high"]), "low": float(b["low"]),
                   "close": c, "volume": float(b.get("volume", 0) or 0)}
        if _post(url, payload, token).get("status") == "accepted":
            last_ms[symbol] = open_ms
            if last_close is not None:
                last_close[symbol] = c
            posted += 1
    return posted


def capture_chart(tv, url, sym, *, token=None, log=print) -> bool:
    """Screenshot the current chart (already switched to `sym`) and POST it to the live service so the
    dashboard can show it. NON-DESTRUCTIVE: no draw, no clear — just a picture of the user's own
    charted template. Never raises."""
    try:
        r = tv.screenshot("chart")
        fp = (r.data or {}).get("file_path") if isinstance(r.data, dict) else None
        if not fp:
            return False
        with open(fp, "rb") as fh:
            png = fh.read()
        return post_chart(url, sym, png, token)
    except Exception as e:
        log(f"{sym}: chart capture skipped ({type(e).__name__})")
        return False


def _pump(tv, url, sym, *, token, last_ms, load_wait, log, last_close=None) -> int:
    """Switch the chart to `sym` (1m) and POST its newly-closed bars. Returns count posted.

    Verifies the chart actually switched before reading, so round-robin never posts a stale
    wrong-symbol bar (set_symbol is async; the chart takes a moment to load). push_new adds a
    price-plausibility guard as a second line of defense against a stale wrong-symbol read."""
    tv.set_symbol(sym)
    tv.set_timeframe("1")
    if load_wait:
        time.sleep(load_wait)
    for _ in range(4):                                            # confirm the switch loaded
        if chart_symbol(tv) == sym:
            return push_new(tv, url, sym, token=token, last_ms=last_ms, last_close=last_close, log=log)
        time.sleep(load_wait or 0.5)
    log(f"{sym}: chart did not switch in time — skipping this cycle")
    return 0


# capture_charts is LEGACY (TradingView-screenshot path) — OFF by default: the dashboard chart is now
# engine-rendered server-side (ict_live/live/chart_render.py), which needs no chart screenshot.
def run(url, *, symbols=None, token=None, interval=15, once=False, load_wait=1.5,
        tv_binary=None, tv_cwd=None, capture_charts=False, log=print) -> dict:
    tv = TvClient(binary=tv_binary, cwd=tv_cwd)
    if not tv.available():
        raise SystemExit("TradingView MCP `tv` CLI not reachable — set TV_CLI and ensure TradingView "
                         "Desktop is running with --remote-debugging-port.")
    if not _reachable(url):
        raise SystemExit(f"live service not reachable at {url} — start it first: "
                         f"python -m ict_live.live.serve")
    wanted = symbols or DEFAULT_SYMBOLS
    unknown = [s for s in wanted if s not in C.INSTRUMENTS]
    if unknown:
        raise SystemExit(f"unknown symbols {unknown}; must be in {sorted(C.INSTRUMENTS)} "
                         f"(e.g. CME_MINI:MNQ1!)")
    if len(wanted) > 1:
        log(f"NOTE: feeding {len(wanted)} symbols round-robin flips the TradingView chart between "
            f"them each cycle — dedicate this chart to the feed.")
    log(f"TradingView real-time feed: {wanted} -> {url}")
    last_ms: dict[str, int] = {}
    last_close: dict[str, float] = get_last_closes(url)   # seed the plausibility guard from the store
    prev = [None]

    def cycle():
        enabled = [s for s in get_enabled(url, wanted) if s in C.INSTRUMENTS]   # dashboard toggles
        if enabled != prev[0]:
            log(f"active symbols now: {enabled}")               # visible when a toggle takes effect
            prev[0] = enabled
        for sym in enabled:
            try:
                k = _pump(tv, url, sym, token=token, last_ms=last_ms, load_wait=load_wait,
                          last_close=last_close, log=log)
                if k:
                    log(f"{sym}: +{k} bars")
                if capture_charts:                              # chart is on `sym` now — snapshot it
                    capture_chart(tv, url, sym, token=token, log=log)
            except Exception as e:
                log(f"{sym}: error {type(e).__name__}: {e}")
        heartbeat(url, SOURCE, dict(last_ms), token)
        return enabled

    en = cycle()
    log(f"seeded from {en}")
    if once:
        return {"symbols": en, "posted": sum(1 for _ in last_ms)}
    while True:
        time.sleep(interval)
        cycle()


def main() -> None:
    ap = argparse.ArgumentParser(description="Real-time TradingView (MCP) feed into the live webhook.")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--symbols", nargs="+", default=None,
                    help=f"chart symbols to round-robin (default {DEFAULT_SYMBOLS})")
    ap.add_argument("--token", default=None)
    ap.add_argument("--interval", type=int, default=15, help="poll seconds")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--tv-screenshots", action="store_true",
                    help="LEGACY: also screenshot the TV chart each cycle (dashboard charts are now "
                         "engine-rendered, so this is off by default)")
    ns = ap.parse_args()
    run(ns.url, symbols=ns.symbols, token=ns.token, interval=ns.interval, once=ns.once,
        capture_charts=ns.tv_screenshots)


if __name__ == "__main__":
    main()
