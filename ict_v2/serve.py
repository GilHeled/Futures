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

from ict_live.storage.market_store import MarketStore
from ict_v2.live import V2Live


class V2Service:
    def __init__(self, data_dir: str):
        self.store_path = Path(data_dir) / "raw_1m.jsonl"
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
        with self._lock:
            store = MarketStore(path=self.store_path)        # re-read the append-only jsonl
            n = 0
            for sym in list(store._bars.keys()):
                live = self.lives.get(sym)
                if live is None:
                    # degenerate-stop floor per instrument: rejects tiny-stop setups so the execution
                    # monitor never surfaces an absurd-RR order (e.g. a 0.25-pt stop with a 290-pt target).
                    try:
                        from ict_live import config as _C
                        mstop = _C.min_stop_for(sym)
                    except Exception:
                        mstop = None
                    live = self.lives.setdefault(sym, V2Live(
                        self.context_tf, self.setup_tf, self.confirm_tf, self.trigger_tf,
                        refine_tf=self.refine_tf, min_stop=mstop, anchor_tf=self.anchor_tf,
                        entry_models=self.entry_models))
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
            self.updated_ms = int(time.time() * 1000)
            return n

    def report(self) -> dict:
        return {"v2": True, "experimental": True, "updated_ms": self.updated_ms, "symbols": self.state}


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
            p = self.path.rstrip("/")
            if p in ("/report", "/v2", ""):
                body = json.dumps(svc.report(), default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
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
