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


def _t(ct, r, ft=None):
    return {"filled": True, "result_R": r, "close_time": ct, "fill_time": ft}


def test_aggregate_richer_metrics():
    # chronological equity: +2,-1,-1,+2,-1  -> peak 2, trough after two losses = 0 -> maxDD 2R
    rows = [_t("2025-01-01T10:00:00+00:00", 2.0, "2025-01-01T09:00:00+00:00"),
            _t("2025-01-02T10:00:00+00:00", -1.0, "2025-01-02T08:00:00+00:00"),
            _t("2025-01-03T10:00:00+00:00", -1.0, "2025-01-03T09:00:00+00:00"),
            _t("2025-01-04T10:00:00+00:00", 2.0, "2025-01-04T08:00:00+00:00"),
            _t("2025-01-05T10:00:00+00:00", -1.0, "2025-01-05T09:00:00+00:00")]
    a = REPORT.aggregate_closed(rows)
    assert a["profit_factor"] == round(4.0 / 3.0, 2)          # gross win 4 / gross loss 3
    assert a["max_drawdown_R"] == 2.0                         # peak 2 -> down to 0
    assert a["longest_loss_streak"] == 2 and a["longest_win_streak"] == 1
    assert a["avg_hold_min"] is not None and a["median_hold_min"] is not None
    # sorting is by close_time, so an out-of-order input still yields the same streaks/DD
    assert REPORT.aggregate_closed(list(reversed(rows)))["max_drawdown_R"] == 2.0


def test_profit_factor_none_without_losses():
    a = REPORT.aggregate_closed([_t("2025-01-01T10:00:00+00:00", 2.0)])
    assert a["profit_factor"] is None                         # no losses -> undefined, reported None


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


def test_reasoning_endpoint_renders_for_a_live_symbol():
    from starlette.testclient import TestClient
    from ict_live.api.webhook import create_app
    runner = _runner_with_state()                       # feeds _h1 bars -> populates last_state["MNQ"]
    assert "MNQ" in runner.last_state                    # MarketState retained per symbol
    c = TestClient(create_app(runner=runner))
    r = c.get("/reasoning", params={"symbol": "MNQ"})
    assert r.status_code == 200 and "MNQ" in r.text      # full inspector HTML for the symbol
    # unknown symbol degrades gracefully (no 500), just says none yet
    r2 = c.get("/reasoning", params={"symbol": "NOPE"})
    assert r2.status_code == 200 and "No reasoning yet" in r2.text
