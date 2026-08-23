"""Replay Runner: the 1m expansion aggregates back exactly, the symbol mapping routes to a known
instrument, and feeding bars through the SAME live components produces a coherent report."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.live import report as REPORT
from ict_live.market.bar import Bar
from ict_live.replay import run as REPLAY

ET = ZoneInfo("America/New_York")


def _agg(payloads):
    o = payloads[0]["open"]
    h = max(p["high"] for p in payloads)
    l = min(p["low"] for p in payloads)
    c = payloads[-1]["close"]
    v = sum(p["volume"] for p in payloads)
    return o, h, l, c, round(v, 6)


def test_to_1m_payloads_aggregates_exactly():
    t0 = datetime(2026, 6, 2, 9, tzinfo=ET)
    for tf, mins in (("5m", 5), ("15m", 15), ("1H", 60)):
        b = Bar(tf, t0, t0 + timedelta(minutes=mins), 20000.0, 20012.5, 19994.0, 20007.25, 300.0)
        ps = REPLAY.to_1m_payloads(b, "CME_MINI:MES1!")
        assert len(ps) == mins
        o, h, l, c, v = _agg(ps)
        assert (o, h, l, c) == (b.open, b.high, b.low, b.close)
        assert abs(v - b.volume) < 1e-6
        # contiguous, 1-minute, well-formed
        assert all(p["resolution"] == "1" for p in ps)
        assert all(p["high"] >= p["open"] >= p["low"] and p["high"] >= p["close"] >= p["low"] for p in ps)


def test_tv_symbol_mapping():
    assert REPLAY.tv_symbol("MES") == "CME_MINI:MES1!"
    assert REPLAY.tv_symbol("MNQ") == "CME_MINI:MNQ1!"
    assert REPLAY.tv_symbol("CME_MINI:NQ1!") == "CME_MINI:NQ1!"


def _h1(n, seed):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(hours=i)
        bars.append(Bar("1H", ot, ot + timedelta(hours=1), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_feed_bars_through_pipeline_produces_report():
    runner = REPLAY.build_runner(signal_tf="1H", entry_tf="15m")
    fed = REPLAY.feed_bars(runner, _h1(60, 6), "CME_MINI:MNQ1!")
    assert fed == 60 * 60                                   # 60 hourly bars -> 3600 one-minute feeds
    rep = REPORT.build_report(runner)
    assert rep["health"]["signal_tf"] == "1H"
    assert len(runner._buf("CME_MINI:MNQ1!")["1H"]) >= 40   # timeframes were built from the 1m feed
    assert runner.recent_signals                            # the engine produced signals
    for key in ("closed_summary", "open_trades", "recent_signals", "closed_trades"):
        assert key in rep
