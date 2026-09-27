"""ICT v2 — advisory side-car service. Reads the SHARED raw-1m store that the existing v1 feed
already fills (no new data pipeline), drives one `V2Live` per symbol, and serves the latest v2
three-stage state as JSON for the dashboard.

EVENT-DRIVEN: it re-ingests the moment the store file changes (a new bar is appended), not on a
periodic timer — so the LTF execution stage re-evaluates as soon as a new LTF bar exists, while the
HTF context / MTF setup only change on their own closes (that cadence lives in MTFEngine). A long
safety re-scan is kept only as a fallback for a missed filesystem event. Read-only w.r.t. v1.

    ICT_V2_DATA_DIR=./ict_live_data ICT_V2_PORT=8020 python -m ict_v2.serve
"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from datetime import datetime, timezone

from ict_live.live.notify import TelegramNotifier
from ict_live.storage.market_store import MarketStore
from ict_v2.live import V2Live
from ict_v2 import scenario_analysis as SA          # read-only analysis layer (never mutates the engine)
from ict_v2 import liquidity_runs as LR             # standalone HRLR/LRLR model (read-only)
from ict_v2 import order_blocks as OB               # standalone Order Blocks model (read-only)
from ict_v2 import market_structure as MS           # standalone BOS/MSS model (read-only)
from ict_v2.scenario_page import PAGE as SCENARIO_PAGE


class V2Service:
    def __init__(self, data_dir: str):
        self.store_path = Path(data_dir) / "raw_1m.jsonl"
        # Telegram alert on each NEW actionable scenario — ARMED (place the resting order) and TRIGGERED
        # (entry live: a confirmed structure reversal or a filled FVG). Operational only; disabled if the
        # env vars are unset. Guards: rising-edge per (scenario, state) + FEED FRESHNESS (only alert while
        # the symbol's feed is live — so a still-armed scenario alerts regardless of how long ago it armed,
        # while frozen-feed symbols and a dead-feed warmup never do).
        self.notifier = TelegramNotifier()
        self._alert_prev: dict[str, set] = {}      # per symbol: set of (scenario_id, state) already alerted
        self.notify_max_age = float(os.environ.get("ICT_V2_NOTIFY_MAX_AGE_SEC", "600"))   # 10 min
        # writable output dir for the trade log (the /data store is read-only for v2). None = disabled.
        _out = os.environ.get("ICT_V2_OUT_DIR", "").strip()
        self.out_dir = None
        if _out:
            try:
                self.out_dir = Path(_out); self.out_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.out_dir = None
        self.lives: dict[str, V2Live] = {}
        self.last_ms: dict[str, int] = {}
        self.state: dict[str, dict] = {}
        self.updated_ms = 0
        self._lock = threading.Lock()
        # OPTIONAL MTF entry-refinement mode: ICT_V2_REFINE=5m refines the 1H entry FVG onto 5m
        # (fresh, unmitigated gaps for fast instruments). Unset/empty = OFF (standard v2 behaviour).
        self.refine_tf = (os.environ.get("ICT_V2_REFINE", "").strip() or None)
        # OPTIONAL Daily/Weekly context anchor: ICT_V2_ANCHOR=D (or W) vetoes a counter-trend 4H bias
        # to neutral (trade only with the higher timeframe). Unset/empty = OFF. NB needs weeks/months
        # of history to actually engage — a Daily range needs several session-days of structure.
        self.anchor_tf = (os.environ.get("ICT_V2_ANCHOR", "").strip() or None)
        # OPTIONAL execution models: ICT_V2_ENTRY_MODELS="fvg,order_block" selects which entry models
        # run. Unset = FVG only. Not-yet-implemented models are inert (see entry_models.REGISTRY).
        em = os.environ.get("ICT_V2_ENTRY_MODELS", "").strip()
        self.entry_models = tuple(x.strip() for x in em.split(",") if x.strip()) or None
        # Cascade TIMEFRAMES (default 4H/1H/15m/1m swing triad). The validated MES edge uses a lower,
        # intraday cascade — set e.g. ICT_V2_SETUP_TF=15m ICT_V2_CONFIRM_TF=5m for the 4H/15m/5m/1m config.
        self.context_tf = os.environ.get("ICT_V2_CONTEXT_TF", "4H").strip() or "4H"
        self.setup_tf = os.environ.get("ICT_V2_SETUP_TF", "1H").strip() or "1H"
        self.confirm_tf = os.environ.get("ICT_V2_CONFIRM_TF", "15m").strip() or "15m"
        self.trigger_tf = os.environ.get("ICT_V2_TRIGGER_TF", "1m").strip() or "1m"
        # VALIDATION / TUNING knobs for the take/skip course filters (NOT course methodology — the
        # faithful defaults are min_rr=2.0, killzone=on, require_retrace=on). Relax these to make the
        # pipeline fire on more setups while eyeballing the logic against charts:
        #   ICT_V2_MIN_RR=1            lower/raise the R:R floor
        #   ICT_V2_KILLZONE=off        stop requiring the manipulation to be in a killzone
        #   ICT_V2_REQUIRE_RETRACE=off let ARMED (un-retraced) setups read as TAKE, not WATCH
        from ict_v2 import recommend as _REC
        _off = lambda v: v.strip().lower() in ("off", "0", "false", "no")
        mr = os.environ.get("ICT_V2_MIN_RR", "").strip()
        _REC.configure(
            min_rr=(float(mr) if mr else None),
            killzone=(False if _off(os.environ.get("ICT_V2_KILLZONE", "")) else None),
            require_retrace=(False if _off(os.environ.get("ICT_V2_REQUIRE_RETRACE", "")) else None))

    def ingest_new(self) -> int:
        """Feed any NEW 1m bars appended to the shared store through each symbol's V2Live. Returns the
        number of bars newly ingested. Serialized (safe to call from several triggers)."""
        if not self.store_path.exists():
            return 0
        pending: list = []                                   # (sym, scenario) armed alerts — sent after unlock
        with self._lock:
            store = MarketStore(path=self.store_path)        # re-read the append-only jsonl
            n = 0
            now = datetime.now(timezone.utc)
            # PRIORITY ORDER: ingest listed symbols first so their read-only rail (P/D · FVG ·
            # scenarios) populates quickly after a restart, before the slower long tail. Override with
            # ICT_V2_PRIORITY (comma-separated); default puts the active MNQ contract(s) first.
            _prio = [s.strip() for s in os.environ.get(
                "ICT_V2_PRIORITY", "CME_MINI:MNQZ2026,CME_MINI:MNQ1!").split(",") if s.strip()]
            _all = list(store._bars.keys())
            _ordered = [s for s in _prio if s in store._bars] + [s for s in _all if s not in _prio]
            for sym in _ordered:
                live = self.lives.get(sym)
                if live is None:
                    # degenerate-stop floor per instrument: rejects tiny-stop setups so the execution
                    # monitor never surfaces an absurd-RR order (e.g. a 0.25-pt stop with a 290-pt target).
                    mstop = pv = None
                    try:
                        from ict_live import config as _C
                        mstop = _C.min_stop_for(sym)
                        inst = _C.INSTRUMENTS.get(sym)          # $ per point (1 contract) for dollar P&L
                        pv = getattr(inst, "point_value", None) if inst is not None else None
                        pdp = (getattr(inst, "price_dp", None) or getattr(inst, "digits", None)
                               if inst is not None else None) or 2
                    except Exception:
                        pass
                    live = self.lives.setdefault(sym, V2Live(
                        self.context_tf, self.setup_tf, self.confirm_tf, self.trigger_tf,
                        refine_tf=self.refine_tf, min_stop=mstop, anchor_tf=self.anchor_tf,
                        entry_models=self.entry_models, point_value=pv, price_dp=pdp))
                last = self.last_ms.get(sym, -1)
                for b in store.bars(sym):                    # chronological
                    ms = int(b.open_time.timestamp() * 1000)
                    if ms <= last:
                        continue
                    live.push_1m(b)                          # <-- LTF close event drives the engine
                    last = ms
                    n += 1
                self.last_ms[sym] = last
                self.state[sym] = live.snapshot()
                if self.notifier.enabled:                    # collect newly-actionable alerts (send after unlock)
                    to_send, seen_now = self._alerts_to_send(sym, self.state[sym], now)
                    self._alert_prev[sym] = seen_now
                    pending.extend((sym, sc, st) for sc, st in to_send)
                # PERSIST the trigger→outcome log per symbol to the WRITABLE out dir (the /data store is
                # mounted read-only for v2). Overwrite = idempotent; the book's trade list is a
                # deterministic function of the replayed bars, so restarts don't duplicate.
                if self.out_dir is not None:
                    try:
                        safe = sym.replace(":", "_").replace("!", "")
                        (self.out_dir / f"v2_trades_{safe}.json").write_text(
                            json.dumps(live.engine.book.trades, default=str))
                    except Exception:
                        pass
            self.updated_ms = int(time.time() * 1000)
        for sym, sc, st in pending:                          # OUTSIDE the lock: a Telegram send never blocks ingest
            try:
                self.notifier.send(self._format_alert(sym, sc, st))
            except Exception:
                pass
        return n

    def _is_recent(self, iso, now) -> bool:
        """True if an ET-iso timestamp is within notify_max_age of `now`."""
        if not iso:
            return False
        try:
            d = datetime.fromisoformat(str(iso))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return 0 <= (now - d).total_seconds() <= self.notify_max_age
        except Exception:
            return False

    def _feed_fresh(self, snap, now) -> bool:
        """The symbol's feed is LIVE if its LATEST BAR is within notify_max_age. This — not the age of the
        arm event — gates alerts: a scenario that armed 40 min ago and is STILL armed on a live feed is
        still actionable and must alert; a frozen-feed symbol (last bar hours old) never alerts."""
        return self._is_recent((snap.get("last") or {}).get("time"), now)

    ALERT_STATES = ("armed", "triggered")     # actionable: place the order / entry is live

    def _alerts_to_send(self, sym, snap, now):
        """RISING EDGE per (scenario, state) on a LIVE FEED. Alert when a scenario ENTERS an actionable
        state — 'armed' (place the resting order) or 'triggered' (entry live: a confirmed structure
        reversal or a filled FVG) — as long as the symbol's FEED IS FRESH, regardless of how long ago the
        state was reached (a still-armed scenario on a live feed is still actionable). Keyed by (id, state)
        so an arm and its later trigger each alert once; frozen-feed symbols never alert."""
        seen_now, out = set(), []
        prev = self._alert_prev.get(sym, set())
        fresh = self._feed_fresh(snap, now)
        for sc in (snap.get("scenarios") or []):
            st = sc.get("state")
            if st not in self.ALERT_STATES:
                continue
            key = (sc.get("id"), st)
            seen_now.add(key)
            if key not in prev and fresh:                    # new (scenario, state) on a live feed → alert
                out.append((sc, st))
        return out, seen_now

    def _format_alert(self, sym, sc, state) -> str:
        """Telegram text for a newly-actionable scenario (operational alert; no trading instruction)."""
        pv = name = None
        try:
            from ict_live import config as C
            name = (C.instrument_names().get(sym, "") if hasattr(C, "instrument_names") else "")
            pv = getattr(C.INSTRUMENTS.get(sym), "point_value", None)   # $ per point (1 contract)
        except Exception:
            name = name or ""
        d = sc.get("direction", "")
        ex = sc.get("position") or sc.get("execution") or {}
        e, s, t, rr = ex.get("entry"), ex.get("stop"), ex.get("target"), ex.get("rr")
        order = ex.get("order") or ("BUY LIMIT" if d == "long" else "SELL LIMIT")
        draw = sc.get("draw") or {}
        at = str((sc.get("events") or {}).get(state, ""))
        tag = "🔫 TRIGGERED" if state == "triggered" else "⏳ ARMED"
        head = f"{'🟢' if d == 'long' else '🔴'} {tag} — {d.upper()} {sym.split(':')[-1]}" + (f" ({name})" if name else "")
        lines = [head,
                 f"Entry {e} · Stop {s} · Target {t}" + (f" · {rr}R" if rr is not None else ""),
                 f"Topstep: {order} @ {e} · SL {ex.get('sl_order', '')} {s} · TP {ex.get('tp_order', '')} {t}"]
        # dollar value of 1R (the risk) and the target reward — per 1 contract, from the instrument point value
        if pv and e is not None and s is not None:
            risk_usd = abs(float(e) - float(s)) * pv
            money = f"1R = ${risk_usd:,.0f} risk"
            if t is not None:
                money += f" · target = +${abs(float(t) - float(e)) * pv:,.0f}"
            lines.append(money + " (1 contract)")
        lines.append(f"→ {draw.get('label', '')} {draw.get('price', '')}")
        if ex.get("why"):
            lines.append(str(ex["why"]))
        if len(at) >= 16:
            lines.append(at[11:16] + " ET")
        return "\n".join(lines)

    def report(self) -> dict:
        # additive, read-only: attach the scenario-analysis picture per symbol from the live buffers.
        # A failure here never affects the engine or the base snapshot — it is caught and surfaced.
        syms = {}
        for sym, snap in self.state.items():
            out = dict(snap)
            live = self.lives.get(sym)
            if live is not None:
                try:
                    out["scenario_analysis"] = SA.analyze(live)
                except Exception as e:                       # pragma: no cover - defensive only
                    out["scenario_analysis"] = {"error": f"{type(e).__name__}: {e}"}
            syms[sym] = out
        return {"v2": True, "experimental": True, "updated_ms": self.updated_ms, "symbols": syms}

    def symbols(self) -> dict:
        """Read-only list of symbols in the store (for the chart's symbol selector), with last bar
        time + close, independent of the slow engine ingest that populates /report."""
        store = MarketStore(path=self.store_path)
        out = []
        for sym in store._bars:
            b = store.bars(sym)
            if not b:
                continue
            out.append({"sym": sym, "last": b[-1].open_time.isoformat(),
                        "bars": len(b), "last_close": b[-1].close})
        out.sort(key=lambda x: x["sym"])
        return {"symbols": out}

    def _build_series(self, sym: str) -> dict:
        """Resample the raw 1m store into all chart timeframes (Bar objects). Read-only."""
        from ict_live.market.bar_builder import BarBuilder
        from ict_live.market.bar import Bar
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        store = MarketStore(path=self.store_path)
        b1 = store.bars(sym) if sym in store._bars else []
        series = {"1m": list(b1), "5m": [], "15m": [], "1H": [], "4H": []}
        builder = BarBuilder(timeframes=("5m", "15m", "1H", "4H"))
        for b in b1:
            for cb in builder.add_1m(b):
                if cb.timeframe in series:
                    series[cb.timeframe].append(cb)
        # 30m: bucket the 1m stream to wall-clock :00/:30 (30m is not a BarBuilder timeframe)
        cur, key, m30 = None, None, []
        for b in b1:
            et = b.open_time.astimezone(ET)
            k = (et.year, et.month, et.day, et.hour, et.minute // 30)
            if k != key:
                if cur is not None:
                    m30.append(cur)
                key = k
                cur = Bar("30m", b.open_time, b.close_time, b.open, b.high, b.low, b.close, b.volume)
            else:
                cur = Bar("30m", cur.open_time, b.close_time, cur.open, max(cur.high, b.high),
                          min(cur.low, b.low), b.close, cur.volume + b.volume)
        if cur is not None:
            m30.append(cur)
        series["30m"] = m30
        return series

    def analyst(self, sym: str) -> dict:
        """Read-only MNQ analyst read (decision-support) across 30m/15m/5m/1m. Not advice/orders."""
        from ict_v2 import analyst as AN
        series = self._build_series(sym)
        as_dicts = {tf: [{"t": x.open_time.isoformat(), "o": x.open, "h": x.high, "l": x.low, "c": x.close}
                         for x in (series.get(tf) or [])[-250:]] for tf in ("30m", "15m", "5m", "1m")}
        return AN.analyze(as_dicts, sym)

    def candles(self, sym: str, tf: str, n: int = 180) -> dict:
        """Read-only candle series for ANY chart timeframe (4H/1H/30m/15m/5m/1m) plus its HRLR/LRLR
        liquidity runs, order blocks and market structure. Never touches the engine or trade generation."""
        series = self._build_series(sym)
        ser = (series.get(tf) or [])[-max(10, min(n, 500)):]
        runs = LR.detect_liquidity_runs(ser, tick=0.25)
        obs = OB.detect_order_blocks(ser, swing_len=3, max_bars=100)
        ms = MS.detect_market_structure(ser, pivot_strength=3)
        return {"symbol": sym, "tf": tf, "tick": 0.25,
                "bars": [{"t": x.open_time.isoformat(), "o": x.open, "h": x.high,
                          "l": x.low, "c": x.close} for x in ser],
                "liquidity_runs": LR.summary(runs),
                "order_blocks": OB.summary(obs, max_bull=3, max_bear=3),
                "market_structure": {**MS.summary(ms, limit=20),
                                      "pivots": MS.detect_pivots(ser, pivot_strength=3)}}


def _start_watchdog(svc: V2Service, dirty: threading.Event):
    """Watch the store file; on any change, signal `dirty`. Returns the Observer, or None if watchdog
    isn't installed (the caller then falls back to a short poll)."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except Exception:
        return None

    name = svc.store_path.name

    class _H(FileSystemEventHandler):
        def on_any_event(self, event):
            if str(getattr(event, "src_path", "")).endswith(name):
                dirty.set()

    obs = Observer()
    svc.store_path.parent.mkdir(parents=True, exist_ok=True)
    obs.schedule(_H(), str(svc.store_path.parent), recursive=False)
    obs.start()
    return obs


def main() -> None:
    data_dir = os.environ.get("ICT_V2_DATA_DIR", "./ict_live_data")
    host = os.environ.get("ICT_V2_HOST", "127.0.0.1")
    port = int(os.environ.get("ICT_V2_PORT", "8020"))
    fallback_poll = float(os.environ.get("ICT_V2_POLL_SEC", "2"))     # only used if watchdog absent
    safety_rescan = float(os.environ.get("ICT_V2_SAFETY_SEC", "30"))  # fallback for a missed fs event

    svc = V2Service(data_dir)
    stop = threading.Event()
    dirty = threading.Event()

    def worker():
        # Event-driven: block until the store changes, then re-ingest (coalescing a burst of appends).
        while not stop.is_set():
            dirty.wait()
            dirty.clear()
            time.sleep(0.15)                                 # coalesce a burst (e.g. warm-up backfill)
            try:
                svc.ingest_new()
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()
    dirty.set()                                              # prime once at startup

    obs = _start_watchdog(svc, dirty)
    if obs is not None:
        mode = f"event-driven (watchdog) + {safety_rescan:g}s safety re-scan"
        def safety():
            while not stop.wait(safety_rescan):
                dirty.set()
    else:
        mode = f"polling every {fallback_poll:g}s (watchdog not installed)"
        def safety():
            while not stop.wait(fallback_poll):
                dirty.set()
    threading.Thread(target=safety, daemon=True).start()

    httpd = ThreadingHTTPServer((host, port), _make_handler(svc))
    print(f"ict_v2 advisory service on http://{host}:{port}  (reads {svc.store_path}; {mode})")
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        if obs is not None:
            obs.stop()


def _make_handler(svc: V2Service):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            from urllib.parse import urlparse
            p = urlparse(self.path).path.rstrip("/")
            if p in ("/report", "/v2", ""):
                body = json.dumps(svc.report(), default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif p == "/analyst":
                from urllib.parse import urlparse, parse_qs
                sym = (parse_qs(urlparse(self.path).query).get("sym") or [""])[0]
                try:
                    payload = svc.analyst(sym)
                except Exception as e:                       # pragma: no cover - defensive
                    payload = {"verdict": "🔴 No Trade", "symbol": sym,
                               "he": ["🔴 No Trade", f"שגיאה: {type(e).__name__}"], "error": str(e)}
                body = json.dumps(payload, default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif p == "/rules":
                from ict_v2 import analyst as AN
                body = json.dumps(AN.rule_inventory(), default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif p == "/symbols":
                body = json.dumps(svc.symbols(), default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif p == "/candles":
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(self.path).query)
                sym = (q.get("sym") or [""])[0]
                tf = (q.get("tf") or ["15m"])[0]
                try:
                    n = int((q.get("n") or ["180"])[0])
                except ValueError:
                    n = 180
                try:
                    payload = svc.candles(sym, tf, n)
                except Exception as e:                       # pragma: no cover - defensive
                    payload = {"symbol": sym, "tf": tf, "bars": [], "error": f"{type(e).__name__}: {e}"}
                body = json.dumps(payload, default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif p == "/scenario":
                body = SCENARIO_PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif p == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()
    return H


if __name__ == "__main__":
    main()
