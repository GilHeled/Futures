"""Replay Dashboard: the JobManager runs the (injected) replay function in the background, tracks
progress, exposes results, and exports a downloadable CSV — and the HTTP layer wires it up. The real
replay engine is never modified; the dashboard only drives it."""
import json
import time

from ict_live.replay import dashboard as DASH
from ict_live.replay import run as REPLAY


class _CT:
    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return self._d


class _Runner:
    """Minimal stand-in for a LiveRunner: recent_signals + trackers[sym].closed[].to_dict()."""
    def __init__(self, sym, trades):
        self.recent_signals = [{}, {}]
        self.trackers = {sym: type("T", (), {"closed": [_CT(t) for t in trades]})()}


def _fake_replay(symbol, start, end, *, period="quarter", progress=None, **kw):
    if progress:
        progress(1, 2)
        progress(2, 2)
    trade = {"symbol": symbol, "direction": "long", "entry": 100.0, "stop": 99.0,
             "exit_target": 102.0, "result": "TARGET", "result_R": 2.0, "win": True, "bars_held": 3,
             "mfe_R": 2.3, "mae_R": 0.4, "filled": True, "fill_time": "2025-01-02T10:00:00-05:00",
             "close_time": "2025-01-02T13:00:00-05:00", "structural_target": 110.0,
             "reasoning": {"execution_score": 0.5, "weakest_factor": "ce_distance",
                           "manipulation": "m", "mss": "s", "fvg": "f", "dealing_range": "dr"}}
    return {"overall": {"scored": 1, "win_rate": 1.0, "expectancy_R": 2.0, "profit_factor": None,
                        "max_drawdown_R": 0.0, "total_R": 2.0, "longest_win_streak": 1,
                        "longest_loss_streak": 0, "avg_hold_min": 180.0, "median_hold_min": 180.0},
            "periods": {"2025-Q1": {"scored": 1}}, "bars_5m": 100, "runner": _Runner(symbol, [trade])}


def test_jobmanager_lifecycle_and_csv(tmp_path):
    jm = DASH.JobManager(replay_fn=_fake_replay, out_dir=str(tmp_path))
    jid = jm.start(["MES", "MNQ"], "2025-01-01", "2025-03-31", period="quarter")
    for _ in range(50):
        if jm.status(jid)["state"] != "running":
            break
        time.sleep(0.02)
    job = jm.status(jid)
    assert job["state"] == "done", job
    assert set(job["results"]) == {"MES", "MNQ"}
    assert job["results"]["MES"]["overall"]["expectancy_R"] == 2.0
    assert job["progress"]["MES"]["done"] == 2
    # a real CSV was exported and is downloadable per symbol
    p = jm.csv_path(jid, "MNQ")
    assert p and p.endswith(".csv")
    header = open(p).readline().strip().split(",")
    assert header == REPLAY.TRADE_CSV_COLS and job["csv"]["MNQ"]["trades"] == 1


def test_error_is_captured_not_raised(tmp_path):
    def boom(*a, **k):
        raise ValueError("bad range")
    jm = DASH.JobManager(replay_fn=boom, out_dir=str(tmp_path))
    jid = jm.start(["MES"], "x", "y")
    for _ in range(50):
        if jm.status(jid)["state"] != "running":
            break
        time.sleep(0.02)
    job = jm.status(jid)
    assert job["state"] == "error" and "bad range" in job["error"]


def test_fetch_live_unreachable_is_graceful():
    # a dead port -> connected False, never raises
    d = DASH._fetch_live("http://127.0.0.1:9", timeout=0.2)
    assert d["connected"] is False and "error" in d


def test_http_handler_run_status_download(tmp_path):
    # drive the handler through an in-process fake socket, no real network
    import io

    jm = DASH.JobManager(replay_fn=_fake_replay, out_dir=str(tmp_path))
    fake_report = {"connected": True, "report": {"closed_summary": {"scored": 1},
                   "recent_signals": [{"action": "SKIP"}], "open_trades": [], "closed_trades": [],
                   "health": {"signal_tf": "1H"}}}
    H = DASH.make_handler(jm, ["MES", "MNQ"], live_fetch=lambda: fake_report)

    class FakeReq:
        def __init__(self, method, path, body=b""):
            self._data = f"{method} {path} HTTP/1.1\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
            self.rfile = io.BytesIO(self._data)
            self.wfile = io.BytesIO()

        def makefile(self, *a, **k):
            return self.rfile

    def call(method, path, body=b""):
        req = FakeReq(method, path, body)
        h = H.__new__(H)
        h.rfile, h.wfile = req.rfile, req.wfile
        h.raw_requestline = h.rfile.readline()
        h.client_address = ("127.0.0.1", 0)
        h.parse_request()
        (h.do_POST if method == "POST" else h.do_GET)()
        out = h.wfile.getvalue().decode(errors="replace")
        return out

    # GET / -> HTML page with both areas + symbols
    page = call("GET", "/")
    assert "ict_live" in page and "LIVE" in page and "REPLAY" in page and "MES" in page
    # GET /live -> proxied live report (SKIP signal visible)
    live = call("GET", "/live")
    body = json.loads(live.split("\r\n\r\n", 1)[1])
    assert body["connected"] is True
    assert body["report"]["recent_signals"][0]["action"] == "SKIP"
    # POST /run -> job id
    body = json.dumps({"symbols": ["MES"], "from": "2025-01-01", "to": "2025-03-31",
                       "period": "quarter"}).encode()
    resp = call("POST", "/run", body)
    jid = json.loads(resp.split("\r\n\r\n", 1)[1])["job_id"]
    for _ in range(50):
        if jm.status(jid)["state"] != "running":
            break
        time.sleep(0.02)
    # GET /status -> done with results; GET /download -> CSV attachment
    st = call("GET", f"/status?job={jid}")
    assert '"state": "done"' in st or '"state":"done"' in st
    dl = call("GET", f"/download?job={jid}&symbol=MES")
    assert "text/csv" in dl and "attachment" in dl and "entry_time" in dl
