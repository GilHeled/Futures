"""Feed bridge — a LOCAL, convenience real-data producer for the live service.

It pulls recent 1-minute bars from yfinance and POSTs them to the live webhook as `ict_live.bar.v1`
payloads — exactly what a TradingView alert would send. This makes the LIVE dashboard populate with
real market data without needing a TradingView account or a public tunnel.

It is NOT the authoritative production feed and adds NO trading logic — it is just another producer of
the same webhook payload, and the frozen engine treats it identically. yfinance futures data is a
continuous-contract proxy that can differ from TradingView and may be delayed; for production, use a
real TradingView 1m alert. Only symbols known to the service (config.INSTRUMENTS) are fed.

  .venv/bin/python -m ict_live.live.feed_bridge --url http://127.0.0.1:8000 --symbols MES MNQ
  .venv/bin/python -m ict_live.live.feed_bridge --once           # backfill recent bars and exit

Runs under the research venv (yfinance/pandas).
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request

from ict_live import config as C

SCHEMA = "ict_live.bar.v1"
SOURCE = "yfinance (continuous-contract proxy, ~10-15 min delayed)"


def _instruments():
    """{root: tv_symbol} for symbols the service accepts (e.g. MES -> CME_MINI:MES1!)."""
    return {inst.root: tv for tv, inst in C.INSTRUMENTS.items()}


def fetch_1m(root: str, period: str = "2d"):
    """Recent 1-minute bars for a futures root via yfinance; list of (open_dt, o,h,l,c,v)."""
    import yfinance as yf
    df = yf.Ticker(f"{root}=F").history(period=period, interval="1m")
    out = []
    for ts, row in df.iterrows():
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        if any(x != x for x in (o, h, l, c)):        # skip NaN rows (gaps)
            continue
        v = float(row.get("Volume", 0) or 0)
        out.append((ts.to_pydatetime(), o, h, l, c, v))
    return out


def _reachable(url: str, timeout: float = 3.0) -> bool:
    """True if the live service answers /health at `url`."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _post_json(full_url: str, payload: dict, token: str | None):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(full_url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _post(url: str, payload: dict, token: str | None):
    return _post_json(url.rstrip("/") + "/webhook/tradingview", payload, token)


def post_chart(url: str, symbol: str, png: bytes, token: str | None = None) -> bool:
    """POST a PNG screenshot for `symbol` to the live service (/chart). Never raises."""
    try:
        full = url.rstrip("/") + "/chart?symbol=" + urllib.parse.quote(symbol)
        headers = {"Content-Type": "image/png"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with urllib.request.urlopen(urllib.request.Request(full, data=png, headers=headers), timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def get_last_bars(url: str) -> dict:
    """{tv_symbol: last stored 1m open_ms} from the service, so warm-up fetches only NEWER bars.

    Returns {} if unreachable — callers then fall back to a full-window backfill."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=5) as r:
            syms = (json.loads(r.read().decode()).get("symbols") or {})
        return {s: v["last_open_ms"] for s, v in syms.items() if v.get("last_open_ms") is not None}
    except Exception:
        return {}


def get_last_closes(url: str) -> dict:
    """{tv_symbol: last stored close} from the service — seeds the feed's plausibility guard from the
    authoritative store (so a bad first read can't set a wrong reference). {} if unreachable."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=5) as r:
            syms = (json.loads(r.read().decode()).get("symbols") or {})
        return {s: v["last_close"] for s, v in syms.items() if v.get("last_close") is not None}
    except Exception:
        return {}


def _period_for_gap(last_open_ms: int, now_ms: int, default: str) -> str:
    """A yfinance period that just covers the gap since the last stored bar (1..7d; 7d is yfinance's
    1-minute limit). Small gaps download a small window; an empty/old store uses the full default."""
    import math
    days = math.ceil(max(0, now_ms - last_open_ms) / 86_400_000)
    return f"{max(1, min(7, days))}d" if days else default


def get_enabled(url: str, default: list) -> list:
    """The dashboard-selected symbol set from the service; falls back to `default` if unset/unreachable."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/feed/control", timeout=3) as r:
            en = json.loads(r.read().decode()).get("enabled")
            return list(en) if en else list(default)
    except Exception:
        return list(default)


def heartbeat(url: str, source: str, bars: dict, token: str | None = None) -> None:
    """Report the active feed source + per-symbol last-bar time so the dashboard can show provenance."""
    try:
        _post_json(url.rstrip("/") + "/feed/heartbeat",
                   {"source": source, "symbols": sorted(bars), "bars": bars}, token)
    except Exception:
        pass


def push_new(url, root, tv_symbol, *, period, token, last_ms: dict, now_ms: int) -> int:
    """POST fully-closed 1m bars newer than the last seen for this symbol. Returns count posted."""
    posted = 0
    for ot, o, h, l, c, v in fetch_1m(root, period):
        open_ms = int(ot.timestamp() * 1000)
        close_ms = open_ms + 60_000
        if open_ms <= last_ms.get(tv_symbol, -1) or close_ms > now_ms:   # only new + fully closed
            continue
        payload = {"schema": SCHEMA, "symbol": tv_symbol, "resolution": "1",
                   "bar_time_ms": open_ms, "bar_close_ms": close_ms,
                   "open": o, "high": h, "low": l, "close": c, "volume": v}
        res = _post(url, payload, token)
        if res.get("status") == "accepted":
            last_ms[tv_symbol] = open_ms
            posted += 1
    return posted


def run(url, symbols, *, token=None, backfill="2d", poll_period="1d", interval=60, once=False,
        log=print) -> dict:
    inst = _instruments()

    def _to_root(s):                                            # accept roots (MES) or keys (CME_MINI:MES1!)
        if s.upper() in inst:
            return s.upper()
        return C.INSTRUMENTS[s].root if s in C.INSTRUMENTS else None
    roots = [r for r in (_to_root(s) for s in symbols) if r]
    if not roots:
        raise SystemExit(f"no known symbols in {symbols}; known roots {sorted(inst)} / "
                         f"keys {sorted(C.INSTRUMENTS)}")
    if not _reachable(url):
        raise SystemExit(f"live service not reachable at {url} — start it first (in another "
                         f"terminal):\n  python3 -m ict_live.live.serve")
    default_keys = [inst[r] for r in roots]                    # control vocabulary = INSTRUMENTS keys
    last_ms: dict[str, int] = {}

    def active_roots():
        enabled = get_enabled(url, default_keys)               # dashboard-selected subset (or default)
        return [r for r in roots if inst[r] in enabled]

    def beat():
        heartbeat(url, SOURCE, {inst[r]: last_ms[inst[r]] for r in roots if inst[r] in last_ms}, token)

    # Warm-up: seed only the DELTA. Ask the service what it already stored and fetch only bars newer
    # than that (always forward of the last stored bar, so the ingestor accepts them — no reset). An
    # empty/unknown store falls back to the full `backfill` window.
    # Prime EVERY requested symbol here (all `roots`), NOT just the dashboard-enabled/streaming subset:
    # warm-up exists to give every symbol history at startup; the streaming loop below still respects
    # the enabled toggles.
    last_store = get_last_bars(url)
    now0 = int(time.time() * 1000)
    total = 0
    for root in roots:
        tv = inst[root]
        ls = last_store.get(tv)
        if ls is not None:
            last_ms[tv] = ls                                   # only post bars newer than stored
            period, kind = _period_for_gap(ls, now0, backfill), "delta"
        else:
            period, kind = backfill, "full"
        n = push_new(url, root, tv, period=period, token=token, last_ms=last_ms, now_ms=now0)
        total += n
        log(f"backfill {root} ({tv}): {n} bars ({kind}, {period})")
    beat()
    if once:
        return {"posted": total, "symbols": roots}
    log(f"streaming every {interval}s (Ctrl+C to stop)…")
    prev = None
    while True:
        time.sleep(interval)
        active = active_roots()
        keys = [inst[r] for r in active]
        if keys != prev:
            log(f"active symbols now: {keys}")                  # visible when a dashboard toggle takes effect
            prev = keys
        for root in active:
            try:
                n = push_new(url, root, inst[root], period=poll_period, token=token,
                             last_ms=last_ms, now_ms=int(time.time() * 1000))
                if n:
                    log(f"{root}: +{n} bars")
                    total += n
            except Exception as e:
                log(f"{root}: poll error {type(e).__name__}: {e}")
        beat()


def main() -> None:
    ap = argparse.ArgumentParser(description="Feed real 1m bars from yfinance into the live webhook.")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--symbols", nargs="+", default=["MES", "MNQ"])
    ap.add_argument("--token", default=None)
    ap.add_argument("--backfill", default="2d", help="yfinance period to backfill on start")
    ap.add_argument("--interval", type=int, default=60, help="poll seconds while streaming")
    ap.add_argument("--once", action="store_true", help="backfill and exit (no streaming)")
    ns = ap.parse_args()
    run(ns.url, ns.symbols, token=ns.token, backfill=ns.backfill, interval=ns.interval, once=ns.once)


if __name__ == "__main__":
    main()
