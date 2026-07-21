import pytest

from mnq_system.config import AccountRiskConfig, ContractSpec
from mnq_system.risk import (
    DailyState,
    check_daily_limits,
    get_position_size,
    get_stop,
    meets_min_reward_risk,
    reward_risk_ratio,
)

CONTRACT = ContractSpec(symbol="MNQ", tick_size=0.25, point_value=2.0)
RISK = AccountRiskConfig()


def test_get_stop_long_is_below_swing_level_by_buffer():
    stop = get_stop(direction="long", swing_level=100.0, tick_size=0.25, buffer_ticks=2)
    assert stop == pytest.approx(99.5)


def test_get_stop_short_is_above_swing_level_by_buffer():
    stop = get_stop(direction="short", swing_level=100.0, tick_size=0.25, buffer_ticks=2)
    assert stop == pytest.approx(100.5)


def test_get_stop_rejects_invalid_direction():
    with pytest.raises(ValueError):
        get_stop(direction="sideways", swing_level=100.0, tick_size=0.25, buffer_ticks=2)


def test_get_position_size_computes_expected_whole_contracts():
    # Arrange: risk $250 (0.5% of $50k), stop distance 10 points, $2/point
    # -> risk per contract = $20 -> 12 contracts (floored from 12.5)
    equity = 50_000.0
    risk_cfg = AccountRiskConfig(risk_pct_per_trade=0.005)

    # Act
    contracts = get_position_size(equity, entry_price=100.0, stop_price=90.0, contract=CONTRACT, risk_cfg=risk_cfg)

    # Assert
    assert contracts == 12


def test_get_position_size_rounds_down_never_up():
    # Arrange: $5 risk budget, $2/contract risk -> 2.5 contracts, must floor to 2
    risk_cfg = AccountRiskConfig(risk_pct_per_trade=0.005)
    equity = 1000.0

    # Act: stop distance 1 point * $2/point = $2/contract risk -> 2.5 contracts
    contracts = get_position_size(equity, entry_price=100.0, stop_price=99.0, contract=CONTRACT, risk_cfg=risk_cfg)

    # Assert
    assert contracts == 2


def test_get_position_size_skips_trade_when_size_rounds_to_zero():
    # Arrange: tiny equity, wide stop -> risk per contract exceeds budget
    risk_cfg = AccountRiskConfig(risk_pct_per_trade=0.005)
    equity = 10.0

    # Act
    contracts = get_position_size(equity, entry_price=100.0, stop_price=50.0, contract=CONTRACT, risk_cfg=risk_cfg)

    # Assert
    assert contracts == 0


def test_get_position_size_zero_when_stop_equals_entry():
    contracts = get_position_size(50_000.0, entry_price=100.0, stop_price=100.0, contract=CONTRACT, risk_cfg=RISK)
    assert contracts == 0


def test_get_position_size_caps_at_max_contracts_for_a_degenerate_tiny_stop():
    # Arrange: a near-zero stop distance would otherwise size up to hundreds
    # of contracts -- max_contracts is the absolute backstop against that.
    risk_cfg = AccountRiskConfig(risk_pct_per_trade=0.005, max_contracts=20)

    # Act: $250 risk budget / (0.25pt * $2/pt = $0.50/contract) = 500 contracts uncapped
    contracts = get_position_size(50_000.0, entry_price=100.0, stop_price=99.75, contract=CONTRACT, risk_cfg=risk_cfg)

    # Assert
    assert contracts == 20


def test_reward_risk_ratio_computes_ratio_of_distances():
    ratio = reward_risk_ratio(entry_price=100.0, stop_price=95.0, target_price=115.0)
    assert ratio == pytest.approx(3.0)


def test_reward_risk_ratio_zero_when_risk_is_zero():
    assert reward_risk_ratio(entry_price=100.0, stop_price=100.0, target_price=110.0) == 0.0


def test_meets_min_reward_risk_true_when_ratio_meets_threshold():
    assert meets_min_reward_risk(100.0, 95.0, 107.5, min_rr=1.5) is True


def test_meets_min_reward_risk_false_when_ratio_below_threshold():
    assert meets_min_reward_risk(100.0, 95.0, 105.0, min_rr=1.5) is False


def test_check_daily_limits_blocks_after_max_daily_loss_hit():
    state = DailyState(daily_pnl=-1600.0)  # -3.2% of 50k, exceeds 3% cap
    assert check_daily_limits(state, account_equity=50_000.0, risk_cfg=RISK) is False


def test_check_daily_limits_blocks_after_max_trades_reached():
    state = DailyState(trades_today=5)
    assert check_daily_limits(state, account_equity=50_000.0, risk_cfg=RISK) is False


def test_check_daily_limits_blocks_after_max_consecutive_losses():
    state = DailyState(consecutive_losses=3)
    assert check_daily_limits(state, account_equity=50_000.0, risk_cfg=RISK) is False


def test_check_daily_limits_allows_trading_within_all_limits():
    state = DailyState(daily_pnl=-100.0, trades_today=1, consecutive_losses=1)
    assert check_daily_limits(state, account_equity=50_000.0, risk_cfg=RISK) is True
