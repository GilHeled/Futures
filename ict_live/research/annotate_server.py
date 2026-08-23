"""Local annotation server: serve the reasoning inspector for a scene and turn my feedback into
training data automatically. Stdlib only (http.server); run locally under the venv.

    .venv/bin/python -m ict_live.research.annotate_server --symbol MNQ --date 2024-01-08 --tf 1H

Opens the reasoning graph for the scene at http://localhost:8765 with the annotation panel wired to
POST /annotate → validated via engine.annotations.make_annotation → appended to the HUMAN-FIDELITY
JSONL (separate from market outcomes). View-only artifacts fall back to showing the JSON to copy.
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from ict_live.engine import pipeline
from ict_live.engine import annotations as anno
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod
from ict_live.research.reasoning_html import render

FIDELITY_PATH = "ict_live/research/datasets/human_fidelity.jsonl"


def _scene_state(symbol: str, date: str, tf: str, window: int = 240):
    """Build the MarketState for the segment/day containing `date` (causal window ending that day)."""
    year = int(date[:4])
    bars5 = data_mod.load_5m(symbol, start=f"{year}-01-01", end=f"{year}-12-31")
    segs = rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))
    target = None
    for seg in segs:
        sig = data_mod.resample(seg.bars, tf)
        for k, b in enumerate(sig):
            if b.open_time.date().isoformat() >= date and k >= 40:
                lo = max(0, k - window + 1)
                target = (seg.contract, sig, lo, k)
                break
        if target:
            break
    if not target:
        raise SystemExit(f"scene {symbol} {date} {tf} not found in cache")
    contract, sig, lo, k = target
    ms = pipeline.analyze(sig[lo:k + 1], tf)
    scene = {"title": f"{symbol} Reasoning Graph", "symbol": symbol, "contract": contract,
             "time": sig[k].open_time.isoformat(), "scene_id": f"{symbol}:{contract}:{tf}:{date}",
             "rr": (f"{ms.ranked_setups[0].item.rr:g}" if ms.ranked_setups else "")}
    return ms, scene


def make_handler(html: str):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, html)

        def do_POST(self):
            if self.path.rstrip("/") != "/annotate":
                return self._send(404, "not found", "text/plain")
            n = int(self.headers.get("Content-Length", 0))
            try:
                p = json.loads(self.rfile.read(n) or b"{}")
                a = anno.make_annotation(candidate_id=p["candidate_id"], decisions=p["decisions"],
                                         annotator=p.get("annotator", "gil"),
                                         error_tags=p.get("error_tags", []), note=p.get("note", ""),
                                         confidence=p.get("confidence"),
                                         candidate_time=p.get("scene_id"))
                anno.append_annotation(FIDELITY_PATH, a)
                self._send(200, json.dumps({"ok": True}), "application/json")
            except Exception as e:
                self._send(400, json.dumps({"ok": False, "error": str(e)}), "application/json")

        def log_message(self, *a):
            pass
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MNQ")
    ap.add_argument("--date", required=True)
    ap.add_argument("--tf", default="1H")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    ms, scene = _scene_state(a.symbol, a.date, a.tf)
    html = render(ms, scene)
    httpd = HTTPServer(("127.0.0.1", a.port), make_handler(html))
    print(f"[annotate] {a.symbol} {a.date} {a.tf} → http://127.0.0.1:{a.port}  "
          f"(decision {ms.recommendation.decision}; annotations → {FIDELITY_PATH})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[annotate] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
