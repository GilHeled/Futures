"""
Tests for horizon_time_exit (mnq_system.strategies.model_driven.base) --
same protective ATR stop as simple_stop_target_exit, but holds for exactly
`owning_horizon` bars instead of a fixed R target, isolating the
profit-taking/duration variable for the "does exit shape matter" comparison.
"""

from dataclasses import replace

import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.candlesticks import Bar
from mnq_system.strategies.model_driven.base import horizon_time_exit
from mnq_system.strategies.model_driven.full_agreement import FullAgreementStrategy
from mnq_system.strategy_api import Position
from tests.strategies.model_driven._fixtures import account, fast_model_driven_config, make_multi_day_bars


def _long_position(entry_bar_pos=10, owning_horizon=5, **overrides):
    defaults = dict(
        direction="long", entry_price=100.0, stop_price=95.0, target_1=110.0, contracts=1, contracts_remaining=1,
        context={"entry_bar_pos": entry_bar_pos, "owning_horizon": owning_horizon},
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_stop_hit_takes_priority_regardless_of_elapsed_bars():
    position = _long_position(entry_bar_pos=10, owning_horizon=5)
    bar = Bar(open=96.0, high=96.5, low=94.5, close=95.5)

    decision = horizon_time_exit(position, bar, current_bar_pos=11)  # only 1 bar elapsed, well within the horizon

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(95.0)


def test_stays_open_before_the_horizon_elapses():
    position = _long_position(entry_bar_pos=10, owning_horizon=5)
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.5)

    decision = horizon_time_exit(position, bar, current_bar_pos=14)  # 4 bars elapsed, horizon is 5

    assert decision.action == "none"


def test_exits_at_market_once_the_horizon_elapses():
    position = _long_position(entry_bar_pos=10, owning_horizon=5)
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.75)

    decision = horizon_time_exit(position, bar, current_bar_pos=15)  # exactly 5 bars elapsed

    assert decision.action == "time_exit"
    assert decision.fill_price == pytest.approx(100.75)  # at the bar's own close, not any stop/target level
    assert decision.fraction == 1.0


def test_never_exits_on_a_full_target_touch_unlike_the_fixed_r_exit():
    # target_1 is still stored on the position (for record-keeping/context
    # parity), but horizon_time_exit never checks it -- profit-taking is
    # purely time-based in this mode.
    position = _long_position(entry_bar_pos=10, owning_horizon=5, target_1=101.0)
    bar = Bar(open=100.5, high=102.0, low=100.0, close=101.5)  # target_1 pierced

    decision = horizon_time_exit(position, bar, current_bar_pos=12)  # inside the horizon

    assert decision.action == "none"


def test_short_position_stop_hit():
    position = _long_position(direction="short", entry_price=100.0, stop_price=105.0, target_1=90.0)
    bar = Bar(open=104.0, high=106.0, low=103.5, close=105.5)

    decision = horizon_time_exit(position, bar, current_bar_pos=11)

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(105.0)


def test_engine_end_to_end_never_exits_via_full_target_in_horizon_time_mode():
    bars = make_multi_day_bars()
    cfg = replace(fast_model_driven_config(), exit_mode="horizon_time")
    acct = account()

    strategy = FullAgreementStrategy(cfg, acct)
    engine = BacktestEngine({"entry": bars}, strategy, acct, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) > 0
    assert all(t.exit_reason in ("stop", "time_exit", "session_flatten") for t in result.trades)


def test_fixed_r_and_horizon_time_share_the_same_signal_calendar():
    # Both exit modes must fire the identical *first* trade -- entry
    # decisions come from the precomputed signal calendar, independent of
    # exit_mode; only the exit rule differs.
    bars = make_multi_day_bars()
    acct = account()

    cfg_fixed_r = fast_model_driven_config()
    cfg_horizon_time = replace(cfg_fixed_r, exit_mode="horizon_time")

    result_fixed_r = BacktestEngine(
        {"entry": bars}, FullAgreementStrategy(cfg_fixed_r, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()
    result_horizon_time = BacktestEngine(
        {"entry": bars}, FullAgreementStrategy(cfg_horizon_time, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()

    assert len(result_fixed_r.trades) > 0 and len(result_horizon_time.trades) > 0
    assert result_fixed_r.trades[0].entry_time == result_horizon_time.trades[0].entry_time
    assert result_fixed_r.trades[0].direction == result_horizon_time.trades[0].direction
