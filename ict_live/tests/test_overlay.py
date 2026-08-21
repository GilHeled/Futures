"""overlay.py + audit bar-conversion: deterministic, correct geometry, no MCP required.

Uses a StubClient that records `draw shape` argument lists so we can assert the exact CLI a
live run would emit, without touching TradingView.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.devtools.tvmcp import overlay
from ict_live.devtools.tvmcp.audit import tv_ohlcv_to_bars
from ict_live.devtools.tvmcp.client import TvResult
from ict_live.structure.swings import Swing

ET = ZoneInfo("America/New_York")


class StubClient:
    def __init__(self):
        self.calls = []
        self._n = 0

    def draw_clear(self):
        self.calls.append(("draw", "clear"))
        return TvResult(True, ["draw", "clear"], {"success": True})

    def run(self, *args):
        self.calls.append(args)
        self._n += 1
        return TvResult(True, list(args), {"success": True, "entity_id": f"E{self._n}"})


def test_from_swings_geometry_and_roles():
    t = datetime(2026, 6, 1, 10, 0, tzinfo=ET)
    swings = [Swing("high", 5, 7, t, 20100.0), Swing("low", 9, 11, t + timedelta(hours=2), 19980.0)]
    anns = overlay.from_swings(swings)
    assert [a.role for a in anns] == ["swing_high", "swing_low"]
    assert anns[0].kind() == "text" and anns[0].price == 20100.0
    assert anns[0].time == int(t.timestamp())
    assert "SH" in anns[0].text and "SL" in anns[1].text


def test_render_emits_expected_cli_and_clears_first():
    t = int(datetime(2026, 6, 1, 10, 0, tzinfo=ET).timestamp())
    anns = [overlay.Annotation("swing_high", 20100.0, t, text="SH 20100"),
            overlay.Annotation("erl_pool", 20000.0, t, text="PDH 20000")]
    c = StubClient()
    res = overlay.render(c, anns, clear=True)
    assert res["count"] == 2 and not res["errors"]
    assert c.calls[0] == ("draw", "clear")                       # cleared first
    # text shape carries --text and --time; hline maps to horizontal_line
    sh = c.calls[1]
    assert sh[:4] == ("draw", "shape", "-t", "text") and "--time" in sh and "--text" in sh
    pool = c.calls[2]
    assert "horizontal_line" in pool and "--time" in pool        # level still needs a time anchor


def test_rect_and_trend_carry_second_point():
    t1, t2 = 1000000000, 1000003600
    rect = overlay.Annotation("fvg", 100.0, t1, price2=101.0, time2=t2)
    args = overlay._args(rect)
    assert "rectangle" in args and "--price2" in args and "--time2" in args
    trend = overlay.Annotation("displacement", 100.0, t1, price2=101.0, time2=t2)
    assert "trend_line" in overlay._args(trend)


def test_roles_present_counts_full_target_set():
    anns = [overlay.Annotation("swing_high", 1, 1), overlay.Annotation("swing_high", 2, 2),
            overlay.Annotation("swing_low", 3, 3)]
    counts = overlay.roles_present(anns)
    assert counts["swing_high"] == 2 and counts["swing_low"] == 1 and counts["fvg"] == 0
    assert set(overlay.ROLE_ORDER) <= set(counts)                # every target role tracked


def test_render_records_errors_without_raising():
    class FailClient(StubClient):
        def run(self, *args):
            self.calls.append(args)
            return TvResult(False, list(args), {"success": False, "error": "boom"})
    c = FailClient()
    res = overlay.render(c, [overlay.Annotation("swing_low", 1.0, 1)], clear=False)
    assert res["count"] == 0 and res["errors"] and res["errors"][0]["error"] == "boom"


def test_from_structural_and_swing_liquidity_and_course():
    from ict_live.structure.significance import ClassifiedSwing
    from ict_live.structure.swing_liquidity import SwingPool
    t = datetime(2026, 6, 1, 10, 0, tzinfo=ET)
    cls = [ClassifiedSwing(Swing("high", 1, 3, t, 20100.0), "structural", True, False, False),
           ClassifiedSwing(Swing("low", 5, 7, t, 19980.0), "structural", False, False, False),
           ClassifiedSwing(Swing("high", 9, 11, t, 20050.0), "rejected", False, False, False)]
    anns = overlay.from_structural(cls)
    assert [a.role for a in anns] == ["swing_dominant", "swing_structural", "swing_rejected"]
    assert anns[0].text.startswith("SH") and anns[2].text == "·"
    assert len(overlay.from_structural(cls, show_rejected=False)) == 2

    pools = [SwingPool("high", 20100.0, t, 1, swept=False, swept_index=None),
             SwingPool("low", 19980.0, t, 5, swept=True, swept_index=8)]
    liq = overlay.from_swing_liquidity(pools)
    assert liq[0].role == "erl_active" and "BSL" in liq[0].text and "active" in liq[0].text
    assert liq[1].role == "erl_swept" and "SSL" in liq[1].text
    assert len(overlay.from_swing_liquidity(pools, show_swept=False)) == 1   # hide swept

    course = overlay.from_course([{"kind": "high", "time": int(t.timestamp()),
                                   "price": 20120.0, "label": "PWH"}])
    assert course[0].role == "course_high" and course[0].text.startswith("C▲")


def test_tv_ohlcv_to_bars_conversion():
    rows = [{"time": 1748782800, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 5},
            {"time": 1748786400, "open": 101, "high": 103, "low": 100, "close": 102, "volume": 6},
            {"time": 1748782800, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 5}]  # dup
    bars, tf = tv_ohlcv_to_bars(rows, "60")
    assert tf == "1H" and len(bars) == 2                          # dedup by time
    assert bars[0].timeframe == "1H"
    assert (bars[0].close_time - bars[0].open_time) == timedelta(minutes=60)
    assert bars[0].open_time < bars[1].open_time                  # ascending
