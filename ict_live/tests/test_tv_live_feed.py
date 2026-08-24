"""TradingView MCP live feed: posts newly-closed 1m bars (skips the forming bar, dedupes), and run()
resolves the chart symbol. TvClient and the HTTP POST are stubbed — no TradingView / network."""
import time

from ict_live.devtools.tvmcp import live_feed as LF


class _R:
    def __init__(self, ok, data=None):
        self.ok, self.data = ok, data


def _fake_tv(bars, symbol="CME_MINI:MNQ1!", available=True):
    class Fake:
        def available(self):
            return available
        def status(self):
            return _R(True, {"chart_symbol": symbol})
        def set_symbol(self, s):
            return _R(True)
        def set_timeframe(self, s):
            return _R(True)
        def ohlcv(self, summary=False):
            return _R(True, {"bars": bars})
    return Fake()


def test_push_new_skips_forming_and_dedupes(monkeypatch):
    now = int(time.time())
    bars = [{"time": now - 180, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"time": now - 120, "open": 1.5, "high": 2.2, "low": 1.4, "close": 2.0, "volume": 12},
            {"time": now - 60,  "open": 2.0, "high": 2.3, "low": 1.9, "close": 2.1, "volume": 8},
            {"time": now,       "open": 2.1, "high": 2.1, "low": 2.0, "close": 2.05, "volume": 1}]  # forming
    posted = []
    monkeypatch.setattr(LF, "_post", lambda url, p, token: (posted.append(p), {"status": "accepted"})[1])
    tv = _fake_tv(bars)
    last = {}
    n = LF.push_new(tv, "http://x", "CME_MINI:MNQ1!", token=None, last_ms=last, log=lambda *a: None)
    assert n == 3 and len(posted) == 3                         # forming bar excluded
    assert all(p["schema"] == "ict_live.bar.v1" and p["resolution"] == "1" for p in posted)
    assert posted[0]["bar_close_ms"] - posted[0]["bar_time_ms"] == 60_000
    # second pass posts nothing new
    assert LF.push_new(tv, "http://x", "CME_MINI:MNQ1!", token=None, last_ms=last, log=lambda *a: None) == 0


def test_run_feeds_requested_symbols(monkeypatch):
    now = int(time.time())
    bars = [{"time": now - 120, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
            {"time": now, "open": 1.5, "high": 1.6, "low": 1.4, "close": 1.5, "volume": 1}]  # forming
    monkeypatch.setattr(LF, "TvClient", lambda **k: _fake_tv(bars))
    monkeypatch.setattr(LF, "_reachable", lambda url, timeout=3.0: True)
    monkeypatch.setattr(LF, "get_enabled", lambda url, default: default)   # no control-plane network
    beats = []
    monkeypatch.setattr(LF, "heartbeat", lambda url, source, bars, token=None: beats.append(source))
    posted = []
    monkeypatch.setattr(LF, "_post", lambda url, p, token: (posted.append(p), {"status": "accepted"})[1])
    rep = LF.run("http://127.0.0.1:8000", symbols=["CME_MINI:MNQ1!"], once=True, load_wait=0,
                 log=lambda *a: None)
    assert rep["symbols"] == ["CME_MINI:MNQ1!"]
    assert [p["symbol"] for p in posted] == ["CME_MINI:MNQ1!"]             # 1 closed bar, forming skipped
    assert beats and "real-time" in beats[0].lower()                      # heartbeat reports source


def test_run_rejects_unknown_symbol(monkeypatch):
    import pytest
    monkeypatch.setattr(LF, "TvClient", lambda **k: _fake_tv([]))
    monkeypatch.setattr(LF, "_reachable", lambda url, timeout=3.0: True)
    with pytest.raises(SystemExit):
        LF.run("http://127.0.0.1:8000", symbols=["FX:EURUSD"], once=True, log=lambda *a: None)


def test_push_new_rejects_wrong_symbol_bar(monkeypatch):
    # a MES-priced bar (~7675) read while the chart is on MNQ (~29000) must NOT be posted
    now = int(time.time())
    bars = [{"time": now - 120, "open": 7674, "high": 7676, "low": 7673, "close": 7675, "volume": 5}]
    posted = []
    monkeypatch.setattr(LF, "_post", lambda url, p, token: (posted.append(p), {"status": "accepted"})[1])
    tv = _fake_tv(bars, symbol="CME_MINI:MNQ1!")
    last_ms, last_close = {}, {"CME_MINI:MNQ1!": 29000.0}      # seeded from the store (real MNQ level)
    n = LF.push_new(tv, "http://x", "CME_MINI:MNQ1!", token=None, last_ms=last_ms,
                    last_close=last_close, log=lambda *a: None)
    assert n == 0 and posted == []                            # 7675 is ~74% off 29000 -> rejected
    assert last_close["CME_MINI:MNQ1!"] == 29000.0            # reference unchanged by a rejected bar


def test_push_new_accepts_plausible_bar_and_updates_ref(monkeypatch):
    now = int(time.time())
    bars = [{"time": now - 120, "open": 29010, "high": 29050, "low": 28990, "close": 29040, "volume": 5}]
    monkeypatch.setattr(LF, "_post", lambda url, p, token: {"status": "accepted"})
    tv = _fake_tv(bars, symbol="CME_MINI:MNQ1!")
    last_ms, last_close = {}, {"CME_MINI:MNQ1!": 29000.0}
    n = LF.push_new(tv, "http://x", "CME_MINI:MNQ1!", token=None, last_ms=last_ms,
                    last_close=last_close, log=lambda *a: None)
    assert n == 1 and last_close["CME_MINI:MNQ1!"] == 29040.0  # accepted + reference advanced
