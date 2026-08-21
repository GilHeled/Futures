"""Ingestion pipeline: auth, schema, routing, dedupe/conflict, ordering, gap, resample,
and replay determinism (live == backtest)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.feeds.ingestor import (ACCEPTED, CONFLICT, DUPLICATE, OUT_OF_ORDER,
                                      REJECTED, Ingestor)

ET = ZoneInfo("America/New_York")
SYM = "CME_MINI:NQ1!"


def _payload(open_et, o=20000.0, h=None, l=None, c=None, v=10.0, symbol=SYM,
             schema="ict_live.bar.v1", resolution="1"):
    c = o + 1.0 if c is None else c
    h = max(o, c) + 0.5 if h is None else h
    l = min(o, c) - 0.5 if l is None else l
    close_et = open_et + timedelta(minutes=1)
    return {"schema": schema, "symbol": symbol, "resolution": resolution,
            "bar_time_ms": int(open_et.timestamp() * 1000),
            "bar_close_ms": int(close_et.timestamp() * 1000),
            "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_auth_required_when_token_set():
    ing = Ingestor(token="secret")
    t = datetime(2026, 6, 1, 20, 0, tzinfo=ET)
    assert ing.ingest(_payload(t), token="wrong").status == REJECTED
    assert ing.ingest(_payload(t), token="secret").status == ACCEPTED


def test_schema_and_resolution_validation():
    ing = Ingestor()
    t = datetime(2026, 6, 1, 20, 0, tzinfo=ET)
    assert ing.ingest(_payload(t, schema="other")).reason.startswith("bad_schema")
    assert ing.ingest(_payload(t, resolution="5")).reason.startswith("bad_resolution")


def test_bad_ohlc_rejected():
    ing = Ingestor()
    t = datetime(2026, 6, 1, 20, 0, tzinfo=ET)
    bad = _payload(t, o=20000, c=20001, h=19999, l=19998)   # high < close
    assert ing.ingest(bad).status == REJECTED


def test_unknown_symbol_rejected():
    ing = Ingestor()
    t = datetime(2026, 6, 1, 20, 0, tzinfo=ET)
    assert ing.ingest(_payload(t, symbol="NASDAQ:AAPL")).reason == "unknown_symbol"


def test_duplicate_and_conflict():
    ing = Ingestor()
    t = datetime(2026, 6, 1, 20, 0, tzinfo=ET)
    assert ing.ingest(_payload(t, o=20000, c=20001)).status == ACCEPTED
    assert ing.ingest(_payload(t, o=20000, c=20001)).status == DUPLICATE       # identical
    r = ing.ingest(_payload(t, o=20000, c=20002))                              # differing
    assert r.status == CONFLICT
    # stored bar unchanged (first-write-wins)
    assert ing.store.get(SYM, int(t.timestamp() * 1000)).close == 20001


def test_out_of_order_stored_not_streamed():
    ing = Ingestor()
    t0 = datetime(2026, 6, 1, 20, 0, tzinfo=ET)
    ing.ingest(_payload(t0))
    ing.ingest(_payload(t0 + timedelta(minutes=1)))
    r = ing.ingest(_payload(t0 - timedelta(minutes=1)))    # older than last
    assert r.status == OUT_OF_ORDER
    assert ing.store.get(SYM, int((t0 - timedelta(minutes=1)).timestamp() * 1000)) is not None


def test_gap_detection_counts_missing_session_minutes():
    ing = Ingestor()
    t0 = datetime(2026, 6, 1, 20, 0, tzinfo=ET)
    ing.ingest(_payload(t0))
    # next bar 5 minutes later -> 4 missing 1m slots (all inside the open session)
    r = ing.ingest(_payload(t0 + timedelta(minutes=5)))
    assert r.status == ACCEPTED and r.gap_minutes == 4
    assert ing.trail.events("missing_bars")


def test_gap_across_maintenance_not_counted():
    ing = Ingestor()
    # last bar Mon 16:59 ET (session open), next bar Mon 18:00 ET (next session) -> the
    # 17:00-18:00 maintenance halt must NOT be counted as missing.
    ing.ingest(_payload(datetime(2026, 6, 1, 16, 59, tzinfo=ET)))
    r = ing.ingest(_payload(datetime(2026, 6, 1, 18, 0, tzinfo=ET)))
    assert r.status == ACCEPTED and r.gap_minutes == 0


def test_pipeline_resamples_and_replay_is_deterministic():
    def run():
        ing = Ingestor()
        t = datetime(2026, 6, 1, 18, 0, tzinfo=ET)     # session open (Tue trade date)
        closed = []
        for i in range(15):
            r = ing.ingest(_payload(t + timedelta(minutes=i)))
            closed += [(b.timeframe, b.open_time, b.close) for b in r.closed_htf]
        return closed
    a, b = run(), run()
    assert a == b                                       # deterministic
    assert any(tf == "5m" for tf, *_ in a)              # 15 one-min bars -> >=2 closed 5m
