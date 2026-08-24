"""Feed bridge: maps known instruments, and posts only NEW, fully-closed 1m bars (dedupe + no
still-forming bar). No network/yfinance in the test — fetch and POST are stubbed."""
from datetime import datetime, timezone

from ict_live.live import feed_bridge as FB


def test_instruments_maps_known_symbols():
    inst = FB._instruments()
    assert inst.get("MES") == "CME_MINI:MES1!" and inst.get("MNQ") == "CME_MINI:MNQ1!"


def test_major_commodities_registered():
    inst = FB._instruments()
    assert inst.get("GC") == "COMEX:GC1!" and inst.get("CL") == "NYMEX:CL1!"
    assert inst.get("SI") == "COMEX:SI1!" and inst.get("NG") == "NYMEX:NG1!"
    # yfinance fetch uses the root as {root}=F
    assert all(r in inst for r in ("GC", "MGC", "CL", "MCL", "NG", "SI", "HG"))


def _bar(minute, price):
    ot = datetime(2026, 8, 21, 10, minute, tzinfo=timezone.utc)
    return (ot, price, price + 1, price - 1, price + 0.5, 100.0)


def test_push_new_only_new_and_closed(monkeypatch):
    bars = [_bar(0, 100), _bar(1, 101), _bar(2, 102)]      # 10:00, 10:01, 10:02 (UTC)
    monkeypatch.setattr(FB, "fetch_1m", lambda root, period="2d": bars)
    posted = []
    monkeypatch.setattr(FB, "_post", lambda url, payload, token: (posted.append(payload),
                                                                  {"status": "accepted"})[1])
    last = {}
    # "now" is 10:02:30 UTC -> the 10:02 bar (closes 10:03) is still forming and must be skipped
    now_ms = int(datetime(2026, 8, 21, 10, 2, 30, tzinfo=timezone.utc).timestamp() * 1000)
    n = FB.push_new("http://x", "MES", "CME_MINI:MES1!", period="2d", token=None,
                    last_ms=last, now_ms=now_ms)
    assert n == 2 and len(posted) == 2                     # 10:00 and 10:01 only
    assert all(p["schema"] == "ict_live.bar.v1" and p["symbol"] == "CME_MINI:MES1!" for p in posted)
    assert posted[0]["resolution"] == "1"
    # a second pass with the same data posts nothing new (dedupe via last_ms)
    n2 = FB.push_new("http://x", "MES", "CME_MINI:MES1!", period="2d", token=None,
                     last_ms=last, now_ms=now_ms)
    assert n2 == 0

    # once the clock advances past 10:03, the previously-forming bar is now closed and gets posted
    later = int(datetime(2026, 8, 21, 10, 5, tzinfo=timezone.utc).timestamp() * 1000)
    n3 = FB.push_new("http://x", "MES", "CME_MINI:MES1!", period="2d", token=None,
                     last_ms=last, now_ms=later)
    assert n3 == 1


def test_run_once_backfills(monkeypatch):
    monkeypatch.setattr(FB, "_reachable", lambda url, timeout=3.0: True)
    monkeypatch.setattr(FB, "get_enabled", lambda url, default: default)   # no control-plane network
    monkeypatch.setattr(FB, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(FB, "fetch_1m", lambda root, period="2d": [_bar(0, 100), _bar(1, 101)])
    monkeypatch.setattr(FB, "_post", lambda url, payload, token: {"status": "accepted"})
    rep = FB.run("http://x", ["MES", "MNQ", "UNKNOWN"], once=True, log=lambda *a: None)
    assert rep["symbols"] == ["MES", "MNQ"] and rep["posted"] == 4    # 2 bars x 2 known symbols


def test_run_accepts_instrument_keys(monkeypatch):
    # run-live.sh passes CME_MINI:…1! keys to both feeds; the yfinance bridge maps them to roots
    monkeypatch.setattr(FB, "_reachable", lambda url, timeout=3.0: True)
    monkeypatch.setattr(FB, "get_enabled", lambda url, default: default)
    monkeypatch.setattr(FB, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(FB, "fetch_1m", lambda root, period="2d": [_bar(0, 100)])
    posted = []
    monkeypatch.setattr(FB, "_post", lambda url, p, token: (posted.append(p["symbol"]), {"status": "accepted"})[1])
    rep = FB.run("http://x", ["CME_MINI:MNQ1!"], once=True, log=lambda *a: None)
    assert rep["symbols"] == ["MNQ"] and posted == ["CME_MINI:MNQ1!"]


def test_get_enabled_falls_back(monkeypatch):
    # unreachable control endpoint -> returns the default set (never raises)
    assert FB.get_enabled("http://127.0.0.1:9", ["CME_MINI:MNQ1!"]) == ["CME_MINI:MNQ1!"]


def test_post_chart_never_raises_when_unreachable():
    assert FB.post_chart("http://127.0.0.1:9", "CME_MINI:MNQ1!", b"x") is False


def test_run_respects_disabled_symbols(monkeypatch):
    # dashboard disabled MNQ -> only MES is fed
    monkeypatch.setattr(FB, "_reachable", lambda url, timeout=3.0: True)
    monkeypatch.setattr(FB, "get_enabled", lambda url, default: ["CME_MINI:MES1!"])
    beats = []
    monkeypatch.setattr(FB, "heartbeat", lambda url, source, bars, token=None: beats.append((source, sorted(bars))))
    monkeypatch.setattr(FB, "fetch_1m", lambda root, period="2d": [_bar(0, 100)])
    posted = []
    monkeypatch.setattr(FB, "_post", lambda url, p, token: (posted.append(p["symbol"]), {"status": "accepted"})[1])
    rep = FB.run("http://x", ["MES", "MNQ"], once=True, log=lambda *a: None)
    assert set(posted) == {"CME_MINI:MES1!"}          # MNQ disabled by the dashboard
    assert beats and "delayed" in beats[0][0].lower()


def test_period_for_gap():
    now = int(datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
    h3 = now - 3 * 3600 * 1000                              # 3h gap -> 1 day window
    d2 = now - int(2.5 * 86400 * 1000)                      # 2.5d gap -> 3 day window
    d30 = now - 30 * 86400 * 1000                           # huge gap -> capped at yfinance's 7d
    assert FB._period_for_gap(h3, now, "7d") == "1d"
    assert FB._period_for_gap(d2, now, "7d") == "3d"
    assert FB._period_for_gap(d30, now, "7d") == "7d"


def test_warmup_fetches_only_the_delta(monkeypatch):
    # the store already holds up to 10:00 -> warm-up posts only the NEWER bars (10:01, 10:02)
    last00 = int(datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(FB, "_reachable", lambda url, timeout=3.0: True)
    monkeypatch.setattr(FB, "get_enabled", lambda url, default: ["CME_MINI:MES1!"])
    monkeypatch.setattr(FB, "get_last_bars", lambda url: {"CME_MINI:MES1!": last00})
    monkeypatch.setattr(FB, "heartbeat", lambda *a, **k: None)
    monkeypatch.setattr(FB, "fetch_1m", lambda root, period="2d": [_bar(0, 100), _bar(1, 101), _bar(2, 102)])
    posted = []
    monkeypatch.setattr(FB, "_post", lambda url, p, token: (posted.append(p["bar_time_ms"]), {"status": "accepted"})[1])
    rep = FB.run("http://x", ["MES"], once=True, log=lambda *a: None)
    assert last00 not in posted                             # the already-stored 10:00 bar is not re-sent
    assert rep["posted"] == 2                               # only the 10:01 and 10:02 delta


def test_run_errors_clearly_when_service_down(monkeypatch):
    import pytest
    monkeypatch.setattr(FB, "_reachable", lambda url, timeout=3.0: False)
    with pytest.raises(SystemExit) as e:
        FB.run("http://127.0.0.1:8000", ["MES"], once=True, log=lambda *a: None)
    assert "not reachable" in str(e.value)
