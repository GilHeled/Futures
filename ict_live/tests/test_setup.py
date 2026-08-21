"""Setup assembly + recommendation: entry/stop/target, RR gate (B4), geometry (A8), NO-TRADE."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live import config as C
from ict_live.structure import ids, ranking
from ict_live.structure.displacement import Displacement
from ict_live.structure.fvg import FVG
from ict_live.structure.manipulation import Sweep
from ict_live.structure.setup import build_setups, recommend
from ict_live.structure.swing_liquidity import SwingPool

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _fvg(direction, ce, status="unfilled", disp_id="D", mss_id="M"):
    return FVG(id="FVGx", direction=direction, top=ce + 1, bottom=ce - 1, ce=ce, mid_index=3,
               formed_index=4, formed_time=T0, status=status, first_touch_index=None,
               reason="x", depends_on=(disp_id, mss_id))


def _disp(direction, sweep_id, did="D"):
    return Displacement(id=did, direction=direction, start_index=5, end_index=8, start_price=0,
                        end_price=0, net=1, span=3, exhausted=True, reason="x",
                        depends_on=(sweep_id,))


def _sweep(extreme, sid="S"):
    return Sweep(id=sid, direction="bearish", pool_price=extreme, extreme=extreme, bar_index=5,
                 time=T0, pool_index=1, close=extreme, reason="x", depends_on=("ERL1H",))


def _pool(kind, price, index):
    return SwingPool(kind, price, T0, index, swept=False, swept_index=None, reason="x")


def test_short_setup_actionable_when_rr_ge_min():
    # short: entry 100 CE, stop=manip extreme 102 (above), target SSL far below for RR>=3
    fvg = _fvg("bearish", 100.0)
    disp = _disp("bearish", "S", "D")
    sweep = _sweep(102.0, "S")               # risk = |102-100| = 2 -> need reward >= 6 -> target <= 94
    active = [_pool("low", 93.0, 2)]         # reward = 7 -> RR 3.5
    setups = build_setups([fvg], {"D": disp}, {"S": sweep}, active, "DR-1H")
    s = setups[0]
    assert s.direction == "short" and s.entry == 100 and s.stop == 102 and s.target == 93
    assert s.rr == 3.5 and s.actionable is True
    assert "FVGx" in s.depends_on and "S" in s.depends_on and "DR-1H" in s.depends_on


def test_reject_when_rr_below_min():
    fvg = _fvg("bearish", 100.0)
    active = [_pool("low", 97.0, 2)]         # reward 3, risk 2 -> RR 1.5 < 3
    s = build_setups([fvg], {"D": _disp("bearish", "S")}, {"S": _sweep(102.0)}, active, None)[0]
    assert s.actionable is False and "RR" in s.reject_reason


def test_reject_mitigated_fvg():
    fvg = _fvg("bearish", 100.0, status="mitigated")
    s = build_setups([fvg], {"D": _disp("bearish", "S")}, {"S": _sweep(102.0)},
                     [_pool("low", 90.0, 2)], None)[0]
    assert not s.actionable and "mitigated" in s.reject_reason


def test_reject_no_target():
    fvg = _fvg("bearish", 100.0)
    s = build_setups([fvg], {"D": _disp("bearish", "S")}, {"S": _sweep(102.0)}, [], None)[0]
    assert not s.actionable and "no opposing" in s.reject_reason.lower()


def test_recommendation_picks_top_actionable_else_no_trade():
    good = _fvg("bearish", 100.0)
    ranked_ok = ranking.rank(
        build_setups([good], {"D": _disp("bearish", "S")}, {"S": _sweep(102.0)},
                     [_pool("low", 92.0, 2)], None),
        [lambda s: ranking.FactorValue("actionable", 1 if s.actionable else 0, "")])
    rec = recommend(ranked_ok)
    assert rec.decision == "SHORT" and rec.setup is not None and rec.depends_on

    bad = _fvg("bearish", 100.0, status="mitigated")
    ranked_bad = ranking.rank(
        build_setups([bad], {"D": _disp("bearish", "S")}, {"S": _sweep(102.0)},
                     [_pool("low", 92.0, 2)], None),
        [lambda s: ranking.FactorValue("actionable", 1 if s.actionable else 0, "")])
    assert recommend(ranked_bad).decision == "NO-TRADE"
