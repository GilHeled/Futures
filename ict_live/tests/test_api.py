"""HTTP round-trip of the FastAPI wrapper (skips if fastapi/httpx test client absent)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("fastapi")
starlette_testclient = pytest.importorskip("starlette.testclient")

from ict_live.api.webhook import create_app
from ict_live.feeds.ingestor import Ingestor

ET = ZoneInfo("America/New_York")


def _client(token=None):
    return starlette_testclient.TestClient(create_app(Ingestor(token=token)))


def _payload(open_et):
    close_et = open_et + timedelta(minutes=1)
    return {"schema": "ict_live.bar.v1", "symbol": "CME_MINI:NQ1!", "resolution": "1",
            "bar_time_ms": int(open_et.timestamp() * 1000),
            "bar_close_ms": int(close_et.timestamp() * 1000),
            "open": 20000.0, "high": 20001.0, "low": 19999.0, "close": 20000.5, "volume": 10}


def test_health_and_status():
    c = _client()
    assert c.get("/health").json() == {"ok": True}
    assert "symbols" in c.get("/status").json()


def test_webhook_accepts_and_auth():
    c = _client(token="tok")
    p = _payload(datetime(2026, 6, 1, 20, 0, tzinfo=ET))
    assert c.post("/webhook/tradingview", json=p).json()["status"] == "rejected"      # no token
    r = c.post("/webhook/tradingview?token=tok", json=p).json()
    assert r["status"] == "accepted" and r["symbol"] == "CME_MINI:NQ1!"
    # bearer header path
    r2 = c.post("/webhook/tradingview", json=_payload(datetime(2026, 6, 1, 20, 1, tzinfo=ET)),
                headers={"Authorization": "Bearer tok"}).json()
    assert r2["status"] == "accepted"
