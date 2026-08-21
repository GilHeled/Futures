"""MSS detector (B6): pre-manipulation structural swing, body-close confirmation, 3 states, deps."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure import ids
from ict_live.structure.displacement import Displacement
from ict_live.structure.mss import detect_mss
from ict_live.structure.swings import Swing

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _bar(i, o, h, l, c):
    return Bar("1H", T0 + timedelta(hours=i), T0 + timedelta(hours=i + 1), o, h, l, c, 1.0)


def _sw(kind, idx, price):
    return Swing(kind, idx, idx + 2, T0 + timedelta(hours=idx), price)


def _disp(direction, start, end):
    return Displacement(id=ids.displacement_id(start, end, direction), direction=direction,
                        start_index=start, end_index=end, start_price=0, end_price=0,
                        net=1, span=end - start, exhausted=True, reason="x",
                        depends_on=("SWP@x",))


def test_bearish_mss_confirmed_by_body_close():
    # structural low at bar 2 (price 95). manip extreme at bar 4; price closes below 95 at bar 6.
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 101, 96, 97), _bar(2, 97, 98, 95, 96),
            _bar(3, 96, 110, 95.5, 108), _bar(4, 108, 112, 106, 107), _bar(5, 107, 108, 96, 97),
            _bar(6, 97, 98, 92, 93)]
    structural = [_sw("low", 2, 95), _sw("high", 4, 112)]
    out = detect_mss([_disp("bearish", 4, 5)], structural, bars)
    assert len(out) == 1
    m = out[0]
    assert m.direction == "bearish" and m.state == "confirmed"
    assert m.broken_price == 95 and m.broken_index == 2 and m.confirm_index == 6
    assert ids.swing_id(_sw("low", 2, 95)) in m.depends_on and "DISP4-5D" in m.depends_on[0]


def test_candidate_when_wick_but_no_close():
    # wick dips below 95 at bar 5 but never closes below -> candidate
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 101, 96, 97), _bar(2, 97, 98, 95, 96),
            _bar(3, 96, 110, 95.5, 108), _bar(4, 108, 112, 106, 107), _bar(5, 107, 108, 93, 99)]
    out = detect_mss([_disp("bearish", 4, 5)], [_sw("low", 2, 95)], bars)
    assert out[0].state == "candidate" and out[0].confirm_index is None


def test_potential_when_no_penetration():
    bars = [_bar(0, 100, 101, 99, 100), _bar(1, 100, 101, 96, 97), _bar(2, 97, 98, 95, 96),
            _bar(3, 96, 110, 96, 108), _bar(4, 108, 112, 106, 107), _bar(5, 107, 108, 100, 105)]
    out = detect_mss([_disp("bearish", 4, 5)], [_sw("low", 2, 95)], bars)
    assert out[0].state == "potential"


def test_target_is_pre_manipulation_only():
    # a structural low AFTER the manip start must not be chosen as the broken swing
    bars = [_bar(i, 100, 105, 90, 95) for i in range(8)]
    structural = [_sw("low", 1, 95), _sw("low", 6, 80)]     # bar6 is after manip start (4)
    out = detect_mss([_disp("bearish", 4, 5)], structural, bars)
    assert out[0].broken_index == 1                         # the pre-manip low, not bar 6
