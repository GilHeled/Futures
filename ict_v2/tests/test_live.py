"""V2Live driven by a 1-minute stream through the existing BarBuilder: per-timeframe cadence —
execution updates every 1m (and every 15m), 1H setup only on 1H closes, 4H context only on 4H
closes — plus a persistable snapshot."""
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
    v = V2Live("4H", "1H", "15m", "1m")
    bars = _1m(1500)                                 # 25 hours of 1m
    prev = {"4H": None, "1H": None, "15m": None, "1m": None}
    counts = {"4H": 0, "1H": 0, "15m": 0, "1m": 0}
    prev_ctx = None
    for b in bars:
        v.push_1m(b)
        for tf in ("4H", "1H", "15m", "1m"):
            if v.updated[tf] != prev[tf]:
                counts[tf] += 1
                prev[tf] = v.updated[tf]
        if v.engine.context is not prev_ctx:         # context changes only on a 4H close
            assert counts["4H"] > 0 and v.updated["4H"] == prev["4H"]
            prev_ctx = v.engine.context
    assert counts["1m"] == len(bars)                 # 1m trigger every minute
    # 4H rarest, then 1H, then 15m, then 1m — strict cadence ordering
    assert 1 <= counts["4H"] < counts["1H"] < counts["15m"] < counts["1m"]


def test_refine_mode_is_optional_and_off_by_default():
    # default: no refine TF, standard 3-TF builder (4H/1H/15m from 1m)
    v = V2Live("4H", "1H", "15m", "1m")
    assert v.refine_tf is None and v.engine.refine_tf is None
    assert "5m" not in v.buf and v.snapshot()["timeframes"]["refine"] is None
    # opt-in: a refine TF is built + buffered and the engine carries the min_stop floor
    vr = V2Live("4H", "1H", "15m", "1m", refine_tf="5m", min_stop=2.0)
    for b in _1m(1500):
        vr.push_1m(b)
    assert vr.refine_tf == "5m" and vr.engine.refine_tf == "5m" and vr.engine.min_stop == 2.0
    assert len(vr.buf["5m"]) > 0                       # the 5m refine buffer accumulates
    assert vr.snapshot()["timeframes"]["refine"] == "5m"


def test_snapshot_is_persistable(tmp_path):
    v = run_bars(_1m(1200))
    snap = v.snapshot()
    assert snap["timeframes"] == {"context": "4H", "setup": "1H", "confirm": "15m", "trigger": "1m",
                                  "refine": None, "anchor": None}     # refine + anchor OFF by default
    assert "setup" in snap and "confirmation" in snap and "execution" in snap
    assert snap["updated"]["1m"] is not None
    p = tmp_path / "v2_state.json"
    v.save(p)
    reloaded = json.loads(p.read_text())
    assert reloaded["timeframes"]["confirm"] == "15m"
