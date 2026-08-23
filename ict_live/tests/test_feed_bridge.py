"""Feed bridge: maps known instruments, and posts only NEW, fully-closed 1m bars (dedupe + no
still-forming bar). No network/yfinance in the test — fetch and POST are stubbed."""
from datetime import datetime, timezone

from ict_live.live import feed_bridge as FB


def test_instruments_maps_known_symbols():
    inst = FB._instruments()
    assert inst.get("MES") == "CME_MINI:MES1!" and inst.get("MNQ") == "CME_MINI:MNQ1!"


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
    monkeypatch.setattr(FB, "fetch_1m", lambda root, period="2d": [_bar(0, 100), _bar(1, 101)])
    monkeypatch.setattr(FB, "_post", lambda url, payload, token: {"status": "accepted"})
    rep = FB.run("http://x", ["MES", "MNQ", "UNKNOWN"], once=True, log=lambda *a: None)
    assert rep["symbols"] == ["MES", "MNQ"] and rep["posted"] == 4    # 2 bars x 2 known symbols
