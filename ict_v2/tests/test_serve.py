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
    svc.poll()
    assert SYM in svc.state
    snap = svc.state[SYM]
    assert snap["updated"]["1m"] is not None and snap["timeframes"]["htf"] == "4H"
    assert "v2" in svc.report() and svc.report()["experimental"] is True


def test_incremental_only_new_bars(tmp_path):
    path = tmp_path / "raw_1m.jsonl"
    store = MarketStore(path=path)
    for b in _1m(300, 0):
        store.append(SYM, b)
    svc = V2Service(str(tmp_path))
    svc.poll()
    first = svc.last_ms[SYM]
    # append more bars to the SAME store, poll again -> processes only the new ones
    store2 = MarketStore(path=path)
    for b in _1m(60, 300, seed=9):
        store2.append(SYM, b)
    svc.poll()
    assert svc.last_ms[SYM] > first                          # advanced, not reprocessed from scratch
