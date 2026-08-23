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


def test_by_period_buckets_by_quarter_and_month():
    closed = [
        {"opened_time": "2025-01-15T10:00:00-05:00", "close_time": "2025-01-15T13:00:00-05:00",
         "filled": True, "result_R": 2.0},
        {"opened_time": "2025-02-20T10:00:00-05:00", "close_time": "2025-02-20T12:00:00-05:00",
         "filled": True, "result_R": -1.0},
        {"opened_time": "2025-04-10T10:00:00-04:00", "close_time": "2025-04-10T12:00:00-04:00",
         "filled": True, "result_R": 2.0},
    ]
    q = REPLAY.by_period(closed, "quarter")
    assert set(q) == {"2025-Q1", "2025-Q2"}
    assert q["2025-Q1"]["scored"] == 2 and q["2025-Q1"]["expectancy_R"] == 0.5
    assert q["2025-Q2"]["scored"] == 1 and q["2025-Q2"]["win_rate"] == 1.0
    m = REPLAY.by_period(closed, "month")
    assert set(m) == {"2025-01", "2025-02", "2025-04"}


def test_render_period_table_smoke():
    overall = REPORT.aggregate_closed([{"filled": True, "result_R": 1.0,
                                        "close_time": "2025-01-01T10:00:00+00:00"}])
    table = REPLAY.render_period_table(overall, {"2025-Q1": overall})
    assert "OVERALL" in table and "2025-Q1" in table and "PF" in table


def test_export_trades_csv(tmp_path):
    import csv as _csv

    class _Tr:                       # minimal stand-in exposing .closed with .to_dict()
        def __init__(self, trades):
            self.closed = trades

    class _CT:
        def __init__(self, d):
            self._d = d

        def to_dict(self):
            return self._d

    trade = {
        "ticket_id": "MNQ:1H:t", "symbol": "CME_MINI:MNQ1!", "direction": "long",
        "entry": 20000.0, "stop": 19996.0, "exit_target": 20008.0, "structural_target": 20050.0,
        "result": "TARGET", "result_R": 2.0, "win": True, "bars_held": 3,
        "mfe_R": 2.4, "mae_R": 0.6, "filled": True,
        "fill_time": "2025-01-02T10:00:00-05:00", "close_time": "2025-01-02T13:00:00-05:00",
        "reasoning": {"execution_score": 0.52, "weakest_factor": "ce_distance",
                      "manipulation": "bullish sweep of 19995.0", "mss": "confirmed bullish @ 20010",
                      "fvg": "bullish unfilled CE 20000 [1H]", "dealing_range": "1H 19980-20020 (up)"},
    }

    class _Runner:
        trackers = {"CME_MINI:MNQ1!": _Tr([_CT(trade)])}
    path = tmp_path / "trades.csv"
    n = REPLAY.export_trades_csv(_Runner(), str(path))
    assert n == 1
    rows = list(_csv.DictReader(open(path)))
    assert list(rows[0].keys()) == REPLAY.TRADE_CSV_COLS
    r = rows[0]
    assert r["direction"] == "long" and r["result_R"] == "2.0" and r["win"] == "True"
    assert r["mfe_R"] == "2.4" and r["mae_R"] == "0.6"
    assert r["target"] == "20008.0"
    assert r["exit_price"] == "20008.0"              # +2R = entry + 2*risk (risk 4) = 20008
    assert r["execution_score"] == "0.52" and r["execution_confidence"] == "0.52"
    assert r["weakest_factor"] == "ce_distance" and "sweep" in r["manipulation"]
    assert r["fvg"].startswith("bullish") and r["dealing_range"].startswith("1H")


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
