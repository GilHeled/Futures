"""Thin FastAPI wrapper over the Ingestor. All logic lives in feeds.ingestor; this file only
maps HTTP <-> IngestResult. FastAPI is imported lazily so importing the engine (and running
its tests) needs no web dependency.

Endpoints:
  POST /webhook/tradingview   ingest one `ict_live.bar.v1` payload -> run the frozen engine on a
                              closed signal-TF bar and track any TAKE (bearer or ?token=)
  GET  /status                per-symbol bar counts + forming state + event count
  GET  /health                liveness
  GET  /report                JSON monitor (open trade, last signals, closed trades, win/expectancy)
  GET  /report.html           the same as one plain HTML page
  GET  /signals               recent trade tickets
  GET  /trades                closed trades

Run: uvicorn ict_live.api.webhook:app  (set ICT_LIVE_TOKEN to require auth, ICT_LIVE_STORE for a
persisted store dir)
"""
# NOTE: no `from __future__ import annotations` — FastAPI must resolve the real Request/Header
# types for dependency injection; stringized annotations break it.
import os
import time
from typing import Optional

from ict_live import config as C
from ict_live.feeds.ingestor import ACCEPTED, Ingestor
from ict_live.live import report as REPORT
from ict_live.live.runner import LiveRunner


def create_app(ingestor: Optional[Ingestor] = None, runner: Optional[LiveRunner] = None,
               store_dir: Optional[str] = None):
    from fastapi import FastAPI, Header, Query, Request
    from fastapi.responses import HTMLResponse, RedirectResponse

    if runner is not None:
        ing = runner.ingestor
    else:
        ing = ingestor or Ingestor(token=os.environ.get("ICT_LIVE_TOKEN") or None)
        runner = LiveRunner(ing, store_dir=store_dir or os.environ.get("ICT_LIVE_STORE") or None)
    app = FastAPI(title="ict_live", version="1")
    app.state.ingestor = ing
    app.state.runner = runner
    # feed control/status plane (operational; a feed reports here, the dashboard drives it)
    app.state.feed = {"status": None, "enabled": None}         # enabled None => feed's own default

    def _token(authorization: Optional[str], token_q: Optional[str]) -> Optional[str]:
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return token_q

    @app.post("/webhook/tradingview")
    async def tradingview(request: Request,
                          authorization: Optional[str] = Header(default=None),
                          token: Optional[str] = Query(default=None)):
        try:
            payload = await request.json()
        except Exception:
            payload = None
        out = runner.feed(payload if isinstance(payload, dict) else {},
                          token=_token(authorization, token))
        ticket = out.get("ticket")
        return {"status": out.get("status"), "reason": out.get("reason"), "symbol": out.get("symbol"),
                "action": (ticket.action if ticket else None),
                "closed_trades": len(out.get("closed_trades", []))}

    @app.get("/status")
    async def status():
        return ing.status()

    @app.get("/")
    async def root():
        return RedirectResponse(url="/report.html")           # convenience: land on the monitor

    @app.get("/health")
    async def health():
        return {"ok": True}

    def _last_price(symbol: str):
        """Freshest close the service holds for a symbol (entry-TF, else signal-TF); None if empty."""
        buf = runner.buffers.get(symbol) or {}
        for tf in (runner.entry_tf, runner.signal_tf):
            bars = buf.get(tf)
            if bars:
                return bars[-1].close
        return None

    @app.get("/report")
    async def report():
        rep = REPORT.build_report(runner)
        rep["feed"] = app.state.feed["status"]                 # which feed, freshness (dashboard shows this)
        rep["enabled"] = app.state.feed["enabled"]
        rep["instruments"] = sorted(C.INSTRUMENTS)
        rep["instrument_names"] = C.instrument_names()
        rep["server_time_ms"] = int(time.time() * 1000)        # for ticket-age display
        rep["last_price"] = {o["symbol"]: _last_price(o["symbol"]) for o in rep["open_trades"]}
        return rep

    # ---- feed control/status (a feed producer reports here; the dashboard toggles symbols) ----
    @app.post("/feed/heartbeat")
    async def feed_heartbeat(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        app.state.feed["status"] = {**(body if isinstance(body, dict) else {}),
                                    "received_ms": int(time.time() * 1000)}
        return {"ok": True}

    @app.get("/feed/control")
    async def feed_control_get():
        return {"enabled": app.state.feed["enabled"], "instruments": sorted(C.INSTRUMENTS)}

    @app.post("/feed/control")
    async def feed_control_set(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        en = body.get("enabled")
        app.state.feed["enabled"] = ([s for s in en if s in C.INSTRUMENTS] if isinstance(en, list)
                                     else None)
        return {"ok": True, "enabled": app.state.feed["enabled"]}

    # ---- seed/live boundary: mark "from now on, bars are live" so warm-up backfill only primes
    #      structure and never surfaces a tradable ticket (operational; engine logic untouched) ----
    @app.get("/live/boundary")
    async def live_boundary():
        return {"live_since_ms": runner.live_since_ms}

    @app.post("/live/mark-now")
    async def live_mark_now(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        ms = body.get("ms") if isinstance(body, dict) else None
        return {"ok": True, "live_since_ms": runner.mark_live(ms)}

    @app.get("/report.html", response_class=HTMLResponse)
    async def report_html():
        return REPORT.render_html(REPORT.build_report(runner))

    @app.get("/signals")
    async def signals():
        return {"recent": runner.recent_signals[-REPORT.RECENT:][::-1]}

    @app.get("/trades")
    async def trades():
        rep = REPORT.build_report(runner)
        return {"closed": rep["closed_trades"], "summary": rep["closed_summary"]}

    return app


def __getattr__(name):
    # lazy module-level `app` so `uvicorn ict_live.api.webhook:app` works without eager import
    if name == "app":
        return create_app()
    raise AttributeError(name)
