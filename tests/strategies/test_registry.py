import pytest

from mnq_system.strategies import get_strategy_spec


def test_ema_fib_reversal_is_marked_regression_test_not_a_live_candidate():
    # No robust, persistent edge was demonstrated for this rule set over a
    # 7-year MNQ backtest -- it must not be picked up as a live candidate by
    # anything that checks StrategySpec.status.
    spec = get_strategy_spec("ema_fib_reversal")

    assert spec.status == "regression_test"


@pytest.mark.parametrize(
    "name", ["liquidity_sweep", "opening_gap", "vwap_reclaim", "overnight_imbalance", "pullback_continuation"]
)
def test_hypothesis_strategies_are_marked_hypothesis_status(name):
    spec = get_strategy_spec(name)

    assert spec.status == "hypothesis"


@pytest.mark.parametrize(
    "name", ["model_full_agreement", "model_weighted", "model_highest_confidence", "model_longest_horizon"]
)
def test_model_driven_policies_are_marked_experimental_not_a_live_candidate(name):
    # The combination policy itself is an untested hypothesis until the
    # decision-level EV validation runs -- none is assumed to be "the
    # answer" going in.
    spec = get_strategy_spec(name)

    assert spec.status == "experimental"


def test_ev_single_horizon_is_marked_experimental_not_a_live_candidate():
    spec = get_strategy_spec("ev_single_horizon")

    assert spec.status == "experimental"
