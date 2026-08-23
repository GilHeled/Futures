"""Monitoring report: JSON + HTML endpoints expose open trade, recent signals, closed trades and the
win-rate / expectancy / avg-R summary, computed from tracker state."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.feeds.ingestor import Ingestor
from ict_live.live import report as REPORT
from ict_live.live.runner import LiveRunner
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")


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


def _runner_with_state(seed=7):
    r = LiveRunner(Ingestor(), signal_tf="1H", window=240)
    for b in _h1(360, seed=seed):
        r.on_closed_bars("MNQ", [b])
    return r


def test_aggregate_closed_math():
    a = REPORT.aggregate_closed([
        {"filled": True, "result_R": 2.0}, {"filled": True, "result_R": -1.0},
        {"filled": True, "result_R": 2.0}, {"filled": False, "result_R": None}])
    assert a["scored"] == 3 and a["wins"] == 2
    assert a["win_rate"] == round(2 / 3, 3) and a["expectancy_R"] == 1.0 and a["no_fill"] == 1


def test_build_report_structure():
    rep = REPORT.build_report(_runner_with_state())
    for key in ("health", "open_trades", "recent_signals", "closed_summary", "closed_trades"):
        assert key in rep
    assert rep["health"]["signal_tf"] == "1H"
    assert len(rep["recent_signals"]) <= REPORT.RECENT
    html = REPORT.render_html(rep)
    assert "ict_live" in html and "win rate" in html and "closed trades" in html


def test_report_endpoints():
    from starlette.testclient import TestClient  # via fastapi
    from ict_live.api.webhook import create_app
    c = TestClient(create_app(runner=_runner_with_state()))
    j = c.get("/report").json()
    assert "closed_summary" in j and "recent_signals" in j
    assert c.get("/report.html").status_code == 200
    assert "win rate" in c.get("/report.html").text
    assert "closed" in c.get("/trades").json()
    assert "recent" in c.get("/signals").json()
