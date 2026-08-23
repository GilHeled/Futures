"""Reliability: the raw-1m store reloads on restart, and build_service assembles + warms up so the
service recovers state from disk with one command."""
from datetime import datetime, timedelta, timezone

from ict_live.live.serve import Config, build_service
from ict_live.market.bar import Bar
from ict_live.storage.market_store import MarketStore

UTC = timezone.utc


def test_market_store_reload_round_trip(tmp_path):
    p = tmp_path / "raw_1m.jsonl"
    s = MarketStore(path=p)
    t0 = datetime(2026, 6, 2, 22, 0, tzinfo=UTC)
    for i in range(5):
        ot = t0 + timedelta(minutes=i)
        s.append("MNQ", Bar("1m", ot, ot + timedelta(minutes=1), 100 + i, 101 + i, 99 + i, 100 + i, 3.0))
    # a fresh store over the same path recovers every bar (same instants + OHLCV)
    s2 = MarketStore(path=p)
    assert s2.count("MNQ") == 5
    b0 = s2.bars("MNQ")[0]
    assert (b0.open, b0.high, b0.low, b0.close) == (100, 101, 99, 100)
    assert b0.open_time == t0


def test_build_service_assembles_and_recovers(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"), token=None)
    # seed a persisted store, then build_service should reload + warm up without error
    store_path = tmp_path / "data" / "raw_1m.jsonl"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    s = MarketStore(path=store_path)
    t0 = datetime(2026, 6, 2, 22, 0, tzinfo=UTC)
    for i in range(120):
        ot = t0 + timedelta(minutes=i)
        s.append("MNQ", Bar("1m", ot, ot + timedelta(minutes=1), 20000, 20002, 19998, 20001, 5.0))
    app, runner, out_cfg = build_service(cfg)
    assert app is not None and out_cfg.signal_tf == "1H"
    # warmup replayed the 120 stored 1m bars -> some hourly bars in the buffer
    assert len(runner._buf("MNQ")["1H"]) >= 1
    h = runner.health()
    assert "MNQ" in h["symbols"]
