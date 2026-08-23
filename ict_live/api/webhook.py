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
from typing import Optional

from ict_live.feeds.ingestor import ACCEPTED, Ingestor
from ict_live.live import report as REPORT
from ict_live.live.runner import LiveRunner


def create_app(ingestor: Optional[Ingestor] = None, runner: Optional[LiveRunner] = None,
               store_dir: Optional[str] = None):
    from fastapi import FastAPI, Header, Query, Request
    from fastapi.responses import HTMLResponse

    if runner is not None:
        ing = runner.ingestor
    else:
        ing = ingestor or Ingestor(token=os.environ.get("ICT_LIVE_TOKEN") or None)
        runner = LiveRunner(ing, store_dir=store_dir or os.environ.get("ICT_LIVE_STORE") or None)
    app = FastAPI(title="ict_live", version="1")
    app.state.ingestor = ing
    app.state.runner = runner

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

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/report")
    async def report():
        return REPORT.build_report(runner)

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
