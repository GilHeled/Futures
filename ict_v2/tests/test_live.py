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
        if v.engine.strategic is not prev_ctx:       # strategic context changes only on a 4H close
            assert counts["4H"] > 0 and v.updated["4H"] == prev["4H"]
            prev_ctx = v.engine.strategic
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
    assert snap["timeframes"] == {"context": "4H", "setup": "1H", "liquidity": "15m", "confirm": "5m",
                                  "trigger": "1m", "refine": None, "anchor": None}  # 5m Lesson-15 confirm, 15m liquidity
    # scenario-centric shape: H4 strategic + H1 intraday context + the 2-3 scenarios
    assert "strategic" in snap and "intraday" in snap and "scenarios" in snap and "scenario_summary" in snap
    assert isinstance(snap["scenarios"], list) and len(snap["scenarios"]) <= 3
    assert snap["updated"]["1m"] is not None
    assert "session" in snap and "killzone" in snap                     # §11 current-session context
    assert snap["session"] in {"asia", "london_active", "ny_am", "ny_pm", ""}
    p = tmp_path / "v2_state.json"
    v.save(p)
    reloaded = json.loads(p.read_text())
    assert reloaded["timeframes"]["confirm"] == "5m" and reloaded["timeframes"]["liquidity"] == "15m"


def test_timeframe_responsibilities_confirm_5m_liquidity_15m():
    """Lesson-15 responsibility split: the trend-change CONFIRMATION is 5m (`confirm_ms`), while liquidity /
    meaningful swings / ORG stay on the 15m LIQUIDITY tf. The ≥15m floor governs liquidity/context, NOT the
    5m confirmation. 1m stays timing-only."""
    v = V2Live()                                          # defaults: 4H / 1H / 15m-liquidity / 5m-confirm / 1m
    assert v.confirm_tf == "5m" and v.liquidity_tf == "15m" and v.engine.confirm_tf == "5m"
    assert v.engine.liquidity_tf == "15m"
    for b in _1m(1500):                                   # 25h of 1m
        v.push_1m(b)
    assert "5m" in v.buf and "15m" in v.buf               # both TFs are built/buffered
    # the WHEN is read from the 5m structure: confirm_ms is the last CLOSED 5m read
    ms = v.engine.confirm_ms
    assert ms is not None and getattr(ms, "tf", "5m") == "5m"
    # the 5m confirm buffer's last bar is not ahead of the 1m cursor (frozen between 5m closes)
    assert v.buf["5m"][-1].close_time <= v.buf["1m"][-1].close_time
    # liquidity buffer (15m) is populated and drives ORG/nearer pools, not the WHEN
    assert v.engine._liquidity_bars is not None and len(v.buf["15m"]) > 0
    # cadence: 5m closes strictly more often than 15m, and 1m more than 5m
    from collections import Counter
    v2c = V2Live()
    cnt = Counter()
    prev = {tf: None for tf in ("15m", "5m", "1m")}
    for b in _1m(1500):
        v2c.push_1m(b)
        for tf in ("15m", "5m", "1m"):
            if v2c.updated.get(tf) != prev[tf]:
                cnt[tf] += 1
                prev[tf] = v2c.updated.get(tf)
    assert cnt["15m"] < cnt["5m"] < cnt["1m"]


def test_liquidity_floor_allows_5m_confirmation_but_not_5m_liquidity():
    """The ≥15m floor (Lesson 6/8) applies to context/setup/liquidity, NOT to the Lesson-15 confirmation."""
    from ict_v2.engine import MTFEngine
    MTFEngine("4H", "1H", "5m", "1m", liquidity_tf="15m")       # ok: 5m confirmation is allowed
    import pytest
    with pytest.raises(ValueError):
        MTFEngine("4H", "1H", "5m", "1m", liquidity_tf="5m")    # 5m as the LIQUIDITY tf violates the floor
