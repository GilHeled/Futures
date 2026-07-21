import pytest

from mnq_system.candlesticks import Bar
from mnq_system.strategies.common import simple_stop_target_exit
from mnq_system.strategy_api import Position


def _long_position(**overrides):
    defaults = dict(direction="long", entry_price=100.0, stop_price=95.0, target_1=110.0, contracts=1, contracts_remaining=1)
    defaults.update(overrides)
    return Position(**defaults)


def test_simple_stop_target_exit_stop_hit_for_long():
    position = _long_position()
    bar = Bar(open=96.0, high=96.5, low=94.5, close=95.5)

    decision = simple_stop_target_exit(position, bar)

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(95.0)
    assert decision.fraction == 1.0


def test_simple_stop_target_exit_full_target_for_long():
    position = _long_position()
    bar = Bar(open=108.0, high=111.0, low=107.5, close=110.5)

    decision = simple_stop_target_exit(position, bar)

    assert decision.action == "full_target"
    assert decision.fill_price == pytest.approx(110.0)


def test_simple_stop_target_exit_stop_takes_priority_over_target_in_the_same_bar():
    position = _long_position()
    bar = Bar(open=100.0, high=111.0, low=94.0, close=100.0)  # both stop and target pierced

    decision = simple_stop_target_exit(position, bar)

    assert decision.action == "stop"


def test_simple_stop_target_exit_none_when_nothing_has_happened():
    position = _long_position()
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.5)

    decision = simple_stop_target_exit(position, bar)

    assert decision.action == "none"


def test_simple_stop_target_exit_short_position_stop_hit():
    position = _long_position(direction="short", entry_price=100.0, stop_price=105.0, target_1=90.0)
    bar = Bar(open=104.0, high=106.0, low=103.5, close=105.5)

    decision = simple_stop_target_exit(position, bar)

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(105.0)
