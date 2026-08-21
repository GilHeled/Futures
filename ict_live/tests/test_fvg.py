"""FVG detector: A1 geometry, same-leg gating, CE, status (unfilled/touched/mitigated), deps."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure import ids
from ict_live.structure.displacement import Displacement
from ict_live.structure.fvg import detect_fvgs
from ict_live.structure.mss import MSS

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _bar(i, o, h, l, c):
    return Bar("1H", T0 + timedelta(hours=i), T0 + timedelta(hours=i + 1), o, h, l, c, 1.0)


def _disp(direction, start, end):
    return Displacement(id=ids.displacement_id(start, end, direction), direction=direction,
                        start_index=start, end_index=end, start_price=0, end_price=0, net=1,
                        span=end - start, exhausted=True, reason="x", depends_on=("SWP@x",))


def _mss(disp_id, direction="bearish"):
    return MSS(id="MSSx", direction=direction, state="confirmed", broken_price=0, broken_index=0,
               confirm_index=5, acceptance=1.0, reason="x", depends_on=(disp_id, "SW0L"))


def test_bearish_fvg_geometry_and_ce():
    # single bearish gap at i=2: low[1]=100 > high[3]=96 -> top=100 bottom=96 ce=98
    bars = [_bar(0, 100, 110, 97, 106), _bar(1, 106, 108, 100, 101), _bar(2, 101, 103, 97, 98),
            _bar(3, 92, 96, 90, 94), _bar(4, 94, 99, 93, 95)]
    d = _disp("bearish", 0, 4)
    fvgs = detect_fvgs([_mss(d.id)], [d], bars)
    assert len(fvgs) == 1
    f = fvgs[0]
    assert f.direction == "bearish" and f.top == 100 and f.bottom == 96 and f.ce == 98
    assert f.mid_index == 2 and f.formed_index == 3 and f.depends_on == (d.id, "MSSx")


def test_bullish_fvg_geometry():
    # single bullish gap at i=2: high[1]=96 < low[3]=99 -> bottom=96 top=99 ce=97.5
    bars = [_bar(0, 90, 95, 89, 91), _bar(1, 91, 96, 90, 95), _bar(2, 95, 101, 94, 100),
            _bar(3, 100, 106, 99, 105), _bar(4, 100, 107, 100, 106)]
    d = _disp("bullish", 0, 4)
    f = detect_fvgs([_mss(d.id, "bullish")], [d], bars)[0]
    assert f.direction == "bullish" and f.bottom == 96 and f.top == 99 and f.ce == 97.5


def test_status_touched_then_mitigated():
    # gap top=100 bottom=96 ce=98; later returns to CE (touch) then body-closes above top (mitigate)
    bars = [_bar(0, 100, 110, 97, 106), _bar(1, 106, 108, 100, 101), _bar(2, 101, 103, 97, 98),
            _bar(3, 92, 96, 90, 94), _bar(4, 92, 97, 91, 93),           # high 97 < CE 98: no touch
            _bar(5, 93, 99, 92, 98),                                    # high 99 >= 98: touched
            _bar(6, 98, 103, 97, 102)]                                  # close 102 > top: mitigated
    d = _disp("bearish", 0, 4)
    f = detect_fvgs([_mss(d.id)], [d], bars)[0]
    assert f.first_touch_index == 5 and f.status == "mitigated"


def test_wrong_direction_gap_not_detected():
    # bearish leg but only a bullish gap exists -> nothing
    bars = [_bar(0, 90, 95, 89, 91), _bar(1, 91, 96, 90, 95), _bar(2, 95, 101, 94, 100),
            _bar(3, 100, 106, 99, 105)]
    assert detect_fvgs([_mss(_disp("bearish", 0, 3).id)], [_disp("bearish", 0, 3)], bars) == []
