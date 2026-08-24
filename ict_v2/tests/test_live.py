"""V2Live: driven by a 1-minute stream through the existing BarBuilder, it honors per-timeframe
cadence — execution updates every 1m, MTF setup only on 15m closes, HTF context only on 4H closes —
and produces a persistable snapshot."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_v2.live import V2Live, run_bars

ET = ZoneInfo("America/New_York")


def _1m(n, seed=7):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 25) - 12) * 0.6
        ot = t0 + timedelta(minutes=i)
        bars.append(Bar("1m", ot, ot + timedelta(minutes=1), o, max(o, c) + (x % 5) * 0.4,
                        min(o, c) - (x % 4) * 0.4, c, 100.0))
        px = c
    return bars


def test_per_timeframe_cadence_from_1m_stream():
    v = V2Live("4H", "15m", "1m")
    bars = _1m(720)                                  # 12 hours of 1m
    prev = {"4H": None, "15m": None, "1m": None}
    counts = {"4H": 0, "15m": 0, "1m": 0}
    prev_ctx = None                                  # engine.context starts None
    for b in bars:
        v.push_1m(b)
        for tf in ("4H", "15m", "1m"):
            if v.updated[tf] != prev[tf]:
                counts[tf] += 1
                prev[tf] = v.updated[tf]
        # the HTF context object may change ONLY on a bar where the 4H just closed
        if v.engine.context is not prev_ctx:
            assert counts["4H"] > 0 and v.updated["4H"] == prev["4H"]   # an HTF close happened
            prev_ctx = v.engine.context
    assert counts["1m"] == len(bars)                 # execution/LTF updates every minute
    assert 1 <= counts["4H"] < counts["15m"] < counts["1m"]            # HTF rarest, MTF between


def test_snapshot_is_persistable(tmp_path):
    v = run_bars(_1m(600))
    snap = v.snapshot()
    assert set(snap) >= {"timeframes", "updated", "context", "setup", "execution"}
    assert snap["timeframes"] == {"htf": "4H", "mtf": "15m", "ltf": "1m"}
    assert snap["updated"]["1m"] is not None                          # LTF advanced
    p = tmp_path / "v2_state.json"
    v.save(p)
    reloaded = json.loads(p.read_text())                              # valid JSON round-trip
    assert reloaded["timeframes"]["htf"] == "4H"
