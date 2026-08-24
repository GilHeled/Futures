"""Engine-rendered chart: render_png produces a real PNG from a MarketState, and the NWOG detector
finds the weekend gap. Pure/offline (matplotlib only; no service)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ict_live.engine import pipeline
from ict_live.live import chart_render
from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")
_PNG = b"\x89PNG\r\n\x1a\n"


def _h1(n, seed):
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=ET)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(hours=i)
        bars.append(Bar("1H", ot, ot + timedelta(hours=1), o, max(o, c) + (x % 7), min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def test_render_png_produces_a_png():
    pytest.importorskip("matplotlib")
    bars = _h1(360, 72)[-240:]
    ms = pipeline.analyze(bars, "1H")
    png = chart_render.render_png(ms, bars, symbol="MNQ")
    assert isinstance(png, (bytes, bytearray)) and png[:8] == _PNG and len(png) > 2000


def test_render_png_handles_empty_marks():
    pytest.importorskip("matplotlib")
    bars = _h1(60, 3)                      # short window -> few/no marks; must still render
    ms = pipeline.analyze(bars, "1H")
    assert chart_render.render_png(ms, bars, symbol="X")[:8] == _PNG


def test_nwog_finds_the_weekend_gap():
    bars = _h1(30, 5)
    assert chart_render._nwog(bars) is None            # continuous hourly bars -> no weekend
    # splice a ~49h gap (Fri close -> Sun open) before the last 10 bars
    t = bars[20].open_time + timedelta(hours=49)
    for j, b in enumerate(bars[20:]):
        ot = t + timedelta(hours=j)
        bars[20 + j] = Bar("1H", ot, ot + timedelta(hours=1), b.open, b.high, b.low, b.close, b.volume)
    ng = chart_render._nwog(bars)
    assert ng is not None and ng[2] == 20 and ng[0] <= ng[1]
