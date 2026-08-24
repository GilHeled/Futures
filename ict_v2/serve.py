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

    def ingest_new(self) -> int:
        """Feed any NEW 1m bars appended to the shared store through each symbol's V2Live. Returns the
        number of bars newly ingested. Serialized (safe to call from several triggers)."""
        if not self.store_path.exists():
            return 0
        with self._lock:
            store = MarketStore(path=self.store_path)        # re-read the append-only jsonl
            n = 0
            for sym in list(store._bars.keys()):
                live = self.lives.get(sym) or self.lives.setdefault(sym, V2Live())
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
