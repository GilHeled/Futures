"""ICT v2 — advisory side-car service. Reads the SHARED raw-1m store that the existing v1 feed
already fills (no new data pipeline), drives one `V2Live` per symbol, and serves the latest v2
three-stage state as JSON for the dashboard. Read-only w.r.t. v1: it only READS the store; it never
writes to it, never touches the v1 service, and the v1 system keeps running normally.

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

    def poll(self) -> None:
        """Re-read the shared store and feed any NEW 1m bars per symbol through its V2Live."""
        if not self.store_path.exists():
            return
        store = MarketStore(path=self.store_path)            # re-read (append-only jsonl)
        for sym in list(store._bars.keys()):
            live = self.lives.get(sym)
            if live is None:
                live = self.lives[sym] = V2Live()
            last = self.last_ms.get(sym, -1)
            for b in store.bars(sym):                        # chronological
                ms = int(b.open_time.timestamp() * 1000)
                if ms <= last:
                    continue
                live.push_1m(b)
                last = ms
            self.last_ms[sym] = last
            self.state[sym] = live.snapshot()
        self.updated_ms = int(time.time() * 1000)

    def report(self) -> dict:
        return {"v2": True, "experimental": True, "updated_ms": self.updated_ms, "symbols": self.state}


def _run_poll_loop(svc: V2Service, interval: float, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            svc.poll()
        except Exception:
            pass
        stop.wait(interval)


def make_handler(svc: V2Service):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.rstrip("/") in ("/report", "/v2", ""):
                body = json.dumps(svc.report(), default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif self.path.rstrip("/") == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()
    return H


def main() -> None:
    data_dir = os.environ.get("ICT_V2_DATA_DIR", "./ict_live_data")
    host = os.environ.get("ICT_V2_HOST", "127.0.0.1")
    port = int(os.environ.get("ICT_V2_PORT", "8020"))
    interval = float(os.environ.get("ICT_V2_POLL_SEC", "10"))
    svc = V2Service(data_dir)
    # Prime in the BACKGROUND (the poll loop polls immediately) so the server is reachable at once;
    # /report returns empty symbols until the first poll finishes replaying the store.
    stop = threading.Event()
    threading.Thread(target=_run_poll_loop, args=(svc, interval, stop), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), make_handler(svc))
    print(f"ict_v2 advisory service on http://{host}:{port}  (reads {svc.store_path}, poll {interval}s)")
    try:
        httpd.serve_forever()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
