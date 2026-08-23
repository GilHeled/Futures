"""END-TO-END ACCEPTANCE — the permanent regression proving the whole system works from a stream of
realistic TradingView 1-minute webhook POSTs through to the monitoring report:

  webhook 1m bars -> BarBuilder timeframes -> frozen engine detects a setup -> TradeTicket (TAKE)
  -> trade opened -> subsequent bars -> closed at the frozen +2R exit -> persisted -> report reflects it.

Deterministic: a fixed seed produces a TAKE within ~30 hourly bars. The continuation is crafted from
the pipeline's OWN emitted ticket (its real entry/stop, read back from the live runner), so the +2R
resolution is guaranteed regardless of BarBuilder session details. Every bar enters through the real
FastAPI webhook endpoint; nothing is stubbed.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.feeds.ingestor import Ingestor
from ict_live.live.runner import LiveRunner
from ict_live.market.bar import Bar
from ict_live.replay.run import to_1m_payloads

ET = ZoneInfo("America/New_York")
SYMBOL = "CME_MINI:MNQ1!"          # a known instrument (config.INSTRUMENTS)
SEED = 6                           # yields a LONG TAKE by hourly bar 29 (offline search)


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


def _feed_hour(client, bar):
    for p in to_1m_payloads(bar, SYMBOL):
        assert client.post("/webhook/tradingview", json=p).json()["status"] == "accepted"


def test_full_lifecycle_webhook_to_report(tmp_path):
    from starlette.testclient import TestClient
    from ict_live.api.webhook import create_app

    hist = _h1(30, SEED)
    t0 = hist[0].open_time
    runner = LiveRunner(Ingestor(), signal_tf="1H", entry_tf="15m", window=240,
                        store_dir=str(tmp_path))
    client = TestClient(create_app(runner=runner))

    # (1) receive realistic 1m webhook bars over HTTP -> (2) BarBuilder builds the timeframes
    #     -> (3) the frozen engine emits a TAKE ticket -> (4) the trade opens.
    for b in hist:
        _feed_hour(client, b)

    takes = [s for s in runner.recent_signals if s["action"] == "TAKE"]
    assert takes, [s["action"] for s in runner.recent_signals]
    tk = takes[-1]
    assert tk["structural"] == "LONG"
    take_id, E, S = tk["ticket_id"], tk["entry"], tk["stop"]
    Rk = E - S
    assert take_id in runner.tracker(SYMBOL).open           # (4) open, awaiting fill

    # (5)+(6) subsequent bars: a fill bar (spans the entry), then a bar that reaches +2R.
    fill = Bar("1H", t0 + timedelta(hours=30), t0 + timedelta(hours=31),
               E, E + 0.25, E - 0.25, E, 100.0)             # contains entry; no stop/target touch
    target = Bar("1H", t0 + timedelta(hours=31), t0 + timedelta(hours=32),
                 E, E + 2 * Rk + 1.0, E, E + 2 * Rk, 100.0)  # reaches +2R, never revisits the stop
    filler = Bar("1H", t0 + timedelta(hours=32), t0 + timedelta(hours=33),
                 E + 2 * Rk, E + 2 * Rk + 0.5, E + 2 * Rk - 0.5, E + 2 * Rk, 100.0)
    for b in (fill, target, filler):
        _feed_hour(client, b)

    # (7) the trade closed at the frozen +2R exit
    closed = {c.ticket_id: c for c in runner.tracker(SYMBOL).closed}
    assert take_id in closed, list(closed)
    win = closed[take_id]
    assert win.result == "TARGET" and win.result_R == 2.0 and win.win is True
    assert win.expected_R == 2.0 and win.direction == "long" and abs(win.entry - E) < 1e-6

    # (8) the completed trade + its signal are persisted to the journal
    assert '"result": "TARGET"' in (tmp_path / "closed_trades.jsonl").read_text()
    assert (tmp_path / "signals.jsonl").read_text().count('"action": "TAKE"') >= 1

    # (9) the monitoring API + page reflect the entire lifecycle
    rep = client.get("/report").json()
    assert rep["closed_summary"]["wins"] >= 1
    assert rep["closed_summary"]["win_rate"] is not None and rep["closed_summary"]["expectancy_R"] is not None
    assert any(c["result"] == "TARGET" for c in rep["closed_trades"])
    assert any(s["action"] == "TAKE" for s in rep["recent_signals"])
    assert rep["health"]["last_signal_bar"].get(SYMBOL) is not None
    html = client.get("/report.html").text
    assert "TARGET" in html and "win rate" in html
