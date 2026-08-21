"""Thin FastAPI wrapper over the Ingestor. All logic lives in feeds.ingestor; this file only
maps HTTP <-> IngestResult. FastAPI is imported lazily so importing the engine (and running
its tests) needs no web dependency.

Endpoints:
  POST /webhook/tradingview   ingest one `ict_live.bar.v1` payload (bearer or ?token=)
  GET  /status                per-symbol bar counts + forming state + event count
  GET  /health                liveness

Run: uvicorn ict_live.api.webhook:app  (set ICT_LIVE_TOKEN to require auth)
"""
# NOTE: no `from __future__ import annotations` — FastAPI must resolve the real Request/Header
# types for dependency injection; stringized annotations break it.
import os
from typing import Optional

from ict_live.feeds.ingestor import ACCEPTED, DUPLICATE, Ingestor


def create_app(ingestor: Optional[Ingestor] = None):
    from fastapi import FastAPI, Header, Query, Request

    ing = ingestor or Ingestor(token=os.environ.get("ICT_LIVE_TOKEN") or None)
    app = FastAPI(title="ict_live ingestion", version="1")
    app.state.ingestor = ing

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
        res = ing.ingest(payload if isinstance(payload, dict) else {}, token=_token(authorization, token))
        return {"status": res.status, "reason": res.reason, "symbol": res.symbol,
                "closed_htf": [b.timeframe for b in res.closed_htf], "gap_minutes": res.gap_minutes}

    @app.get("/status")
    async def status():
        return ing.status()

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


def __getattr__(name):
    # lazy module-level `app` so `uvicorn ict_live.api.webhook:app` works without eager import
    if name == "app":
        return create_app()
    raise AttributeError(name)
