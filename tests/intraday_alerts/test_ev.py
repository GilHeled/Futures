import numpy as np

from intraday_alerts.ev import expected_value, round_trip_cost


def test_dollar_units_and_symmetry():
    # certain UP: long should win k*ATR*point_value minus cost; short loses.
    ev = expected_value(p_up=1.0, p_down=0.0, p_timeout=0.0, k=1.0, atr=2.0,
                        tret=0.0, point_value=5.0, rt_cost=1.5)
    assert np.isclose(ev.ev_long, 1.0 * 2.0 * 5.0 - 1.5)     # +8.5
    assert np.isclose(ev.ev_short, -1.0 * 2.0 * 5.0 - 1.5)   # -11.5
    assert ev.best_side == "long"


def test_timeout_short_is_negative_of_long():
    ev = expected_value(p_up=0.0, p_down=0.0, p_timeout=1.0, k=1.0, atr=2.0,
                        tret=0.7, point_value=5.0, rt_cost=0.0)
    assert np.isclose(ev.ev_long, 0.7 * 5.0)
    assert np.isclose(ev.ev_short, -0.7 * 5.0)


def test_tret_zero_sensitivity_gate():
    full = expected_value(0.0, 0.0, 1.0, 1.0, 2.0, tret=0.7, point_value=5.0, rt_cost=0.0)
    zero = expected_value(0.0, 0.0, 1.0, 1.0, 2.0, tret=0.7, point_value=5.0, rt_cost=0.0, tret_zero=True)
    assert full.ev_long != 0.0 and zero.ev_long == 0.0   # timeout term removed


def test_no_side_when_both_ev_nonpositive():
    ev = expected_value(0.34, 0.33, 0.33, 1.0, 1.0, tret=0.0, point_value=5.0, rt_cost=100.0)
    assert ev.best_side is None


def test_round_trip_cost_dollars():
    # 1.5 commission + (1 spread + 2*1 slippage)*0.25 tick * 5 pt_value
    assert np.isclose(round_trip_cost(1.5, 1.0, 1.0, 0.25, 5.0), 1.5 + 3 * 0.25 * 5.0)
