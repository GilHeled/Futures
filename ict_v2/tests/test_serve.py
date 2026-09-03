"""V2Service reads the shared raw-1m store and drives V2Live per symbol, incrementally (only new
bars), producing a per-symbol snapshot. It only READS the store — never writes it."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.storage.market_store import MarketStore
from ict_v2.serve import V2Service

ET = ZoneInfo("America/New_York")
SYM = "CME_MINI:MNQ1!"


def _1m(n, start, seed=7):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET) + timedelta(minutes=start)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 25) - 12) * 0.6
        ot = t0 + timedelta(minutes=i)
        bars.append(Bar("1m", ot, ot + timedelta(minutes=1), o, max(o, c) + (x % 5) * 0.4,
                        min(o, c) - (x % 4) * 0.4, c, 100.0))
        px = c
    return bars


def test_reads_shared_store_and_snapshots(tmp_path):
    store = MarketStore(path=tmp_path / "raw_1m.jsonl")
    for b in _1m(400, 0):
        store.append(SYM, b)
    svc = V2Service(str(tmp_path))
    svc.ingest_new()
    assert SYM in svc.state
    snap = svc.state[SYM]
    assert snap["updated"]["1m"] is not None and snap["timeframes"]["context"] == "4H"
    assert "v2" in svc.report() and svc.report()["experimental"] is True


def test_alert_rising_edge_and_recency_armed_and_triggered(tmp_path):
    """Alerts fire once per NEW (scenario, actionable state) and only for RECENT events. 'armed' AND
    'triggered' both alert (a structure entry triggers directly, skipping armed)."""
    from datetime import timezone
    svc = V2Service(str(tmp_path)); svc.notify_max_age = 600
    now = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    def sc(sid, state, min_ago=None):
        ev = {state: (now - timedelta(minutes=min_ago)).isoformat()} if min_ago is not None else {}
        return {"id": sid, "state": state, "direction": "long", "events": ev,
                "execution": {"entry": 1, "stop": 0.5, "target": 3, "rr": 4}, "draw": {"label": "BSL", "price": 3}}
    # fresh armed (2m) → alert; a fresh TRIGGERED (1m) → alert; old armed (30m) → recency-skipped; watching → ignored
    to_send, seen = svc._alerts_to_send(SYM, [sc("a", "armed", 2), sc("t", "triggered", 1),
                                              sc("b", "armed", 30), sc("c", "watching")], now)
    assert sorted((x[0]["id"], x[1]) for x in to_send) == [("a", "armed"), ("t", "triggered")]
    assert seen == {("a", "armed"), ("t", "triggered"), ("b", "armed")}
    svc._alert_prev[SYM] = seen
    # next cycle: 'a' still armed → NOT re-sent; 'a' now TRIGGERED (new state) → alert; a new armed 'd' → alert
    to_send2, _ = svc._alerts_to_send(SYM, [sc("a", "triggered", 1), sc("d", "armed", 1)], now)
    assert sorted((x[0]["id"], x[1]) for x in to_send2) == [("a", "triggered"), ("d", "armed")]


def test_format_alert_armed_and_triggered(tmp_path):
    svc = V2Service(str(tmp_path))
    base = {"id": "x", "direction": "long",
            "execution": {"entry": 4376.75, "stop": 4375.2, "target": 4420.05, "rr": 27.94, "order": "BUY LIMIT",
                          "sl_order": "sell stop", "tp_order": "sell limit", "why": "awaiting price to fall 37 pts"},
            "draw": {"label": "BSL", "price": 4423.4}}
    a = dict(base, state="armed", events={"armed": "2026-09-02T15:04:00-04:00"})
    m = svc._format_alert("COMEX_MINI:MGC1!", a, "armed")
    assert "ARMED" in m and "LONG" in m and "MGC1!" in m and "BUY LIMIT @ 4376.75" in m and "27.94R" in m and "15:04 ET" in m
    # $ value of 1R (risk) and the target reward — MGC = $10/pt: risk 1.55pt→$16, reward 43.3pt→$433
    assert "1R = $" in m and "risk" in m and "target = +$" in m and "(1 contract)" in m
    t = dict(base, state="triggered", events={"triggered": "2026-09-02T15:06:00-04:00"})
    assert "TRIGGERED" in svc._format_alert("COMEX_MINI:MGC1!", t, "triggered")


def test_incremental_only_new_bars(tmp_path):
    path = tmp_path / "raw_1m.jsonl"
    store = MarketStore(path=path)
    for b in _1m(300, 0):
        store.append(SYM, b)
    svc = V2Service(str(tmp_path))
    svc.ingest_new()
    first = svc.last_ms[SYM]
    # append more bars to the SAME store, poll again -> processes only the new ones
    store2 = MarketStore(path=path)
    for b in _1m(60, 300, seed=9):
        store2.append(SYM, b)
    svc.ingest_new()
    assert svc.last_ms[SYM] > first                          # advanced, not reprocessed from scratch
