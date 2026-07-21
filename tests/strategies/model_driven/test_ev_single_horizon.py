"""
Tests for EVSingleHorizonStrategy (mnq_system.strategies.model_driven.
ev_single_horizon) -- an expected-value decision rule on the model's full
predicted distribution, tested at a single horizon with no cross-horizon
aggregation.
"""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.candlesticks import Bar
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig
from mnq_system.strategies.model_driven.ev_single_horizon import (
    EVSingleHorizonConfig,
    EVSingleHorizonStrategy,
    dynamic_ev_exit,
)
from mnq_system.strategy_api import Position
from tests.strategies.model_driven._fixtures import FAST_FEATURE_CFG, account, make_multi_day_bars


def _fast_ev_config(**overrides) -> EVSingleHorizonConfig:
    defaults = dict(
        exit=HypothesisExitConfig(atr_period=3), feature_cfg=FAST_FEATURE_CFG, horizon=10,
        n_folds=3, min_train_fraction=0.3, debounce_bars=2,
    )
    defaults.update(overrides)
    return EVSingleHorizonConfig(**defaults)


def _series(bars, values):
    return pd.Series(values, index=bars.index, dtype=float)


def test_goes_long_when_ev_clears_the_hurdle_positively():
    bars = make_multi_day_bars(n_days=5)
    strategy = EVSingleHorizonStrategy(_fast_ev_config(), account())
    strategy.bars_entry = bars
    strategy._ev = _series(bars, 0.5)
    strategy._cost_hurdle = _series(bars, 0.2)

    raw = strategy._build_raw_signal_series()

    assert (raw["direction"] == 1).all()
    assert (raw["strength"] == 0.5).all()


def test_goes_short_when_ev_clears_the_hurdle_negatively():
    bars = make_multi_day_bars(n_days=5)
    strategy = EVSingleHorizonStrategy(_fast_ev_config(), account())
    strategy.bars_entry = bars
    strategy._ev = _series(bars, -0.5)
    strategy._cost_hurdle = _series(bars, 0.2)

    raw = strategy._build_raw_signal_series()

    assert (raw["direction"] == -1).all()


def test_stands_aside_when_ev_does_not_clear_the_hurdle():
    bars = make_multi_day_bars(n_days=5)
    strategy = EVSingleHorizonStrategy(_fast_ev_config(), account())
    strategy.bars_entry = bars
    strategy._ev = _series(bars, 0.1)
    strategy._cost_hurdle = _series(bars, 0.2)

    raw = strategy._build_raw_signal_series()

    assert (raw["direction"] == 0).all()


def test_stands_aside_exactly_at_the_hurdle_boundary():
    bars = make_multi_day_bars(n_days=5)
    strategy = EVSingleHorizonStrategy(_fast_ev_config(), account())
    strategy.bars_entry = bars
    strategy._ev = _series(bars, 0.2)
    strategy._cost_hurdle = _series(bars, 0.2)  # ev == hurdle, not strictly greater

    raw = strategy._build_raw_signal_series()

    assert (raw["direction"] == 0).all()


def test_higher_cost_hurdle_can_flip_a_signal_to_stand_aside():
    bars = make_multi_day_bars(n_days=5)
    ev = 0.3
    strategy_cheap = EVSingleHorizonStrategy(_fast_ev_config(), account())
    strategy_cheap.bars_entry = bars
    strategy_cheap._ev = _series(bars, ev)
    strategy_cheap._cost_hurdle = _series(bars, 0.1)  # cheap: ev clears it

    strategy_expensive = EVSingleHorizonStrategy(_fast_ev_config(), account())
    strategy_expensive.bars_entry = bars
    strategy_expensive._ev = _series(bars, ev)
    strategy_expensive._cost_hurdle = _series(bars, 0.5)  # expensive: same ev no longer clears it

    assert (strategy_cheap._build_raw_signal_series()["direction"] == 1).all()
    assert (strategy_expensive._build_raw_signal_series()["direction"] == 0).all()


def test_raw_signal_is_nan_wherever_ev_or_hurdle_is_not_yet_available():
    bars = make_multi_day_bars(n_days=5)
    strategy = EVSingleHorizonStrategy(_fast_ev_config(), account())
    strategy.bars_entry = bars
    ev = _series(bars, 0.5)
    ev.iloc[:10] = np.nan  # no OOS prediction yet for the first 10 bars
    strategy._ev = ev
    strategy._cost_hurdle = _series(bars, 0.2)

    raw = strategy._build_raw_signal_series()

    assert raw["direction"].iloc[:10].isna().all()
    assert (raw["direction"].iloc[10:] == 1).all()


def test_cost_hurdle_formula_scales_with_atr_and_realistic_costs():
    # hurdle(t) = round_trip_cost_dollars / (atr(t) * point_value)
    bars = make_multi_day_bars(n_days=20)
    acct = account()
    cfg = _fast_ev_config(commission_per_contract=1.5, slippage_ticks=1.0)
    strategy = EVSingleHorizonStrategy(cfg, acct)

    strategy.precompute_batch({"entry": bars})

    round_trip_cost_dollars = 1.5 + 2 * 1.0 * acct.contract.tick_size * acct.contract.point_value
    from mnq_system.indicators import atr as atr_fn
    atr_series = atr_fn(bars, period=cfg.feature_cfg.atr_period)
    expected_hurdle = round_trip_cost_dollars / (atr_series * acct.contract.point_value)

    valid = strategy._cost_hurdle.notna() & expected_hurdle.notna()
    assert valid.any()
    pd.testing.assert_series_equal(
        strategy._cost_hurdle[valid], expected_hurdle[valid], check_names=False
    )


def test_zero_cost_gives_a_zero_hurdle():
    bars = make_multi_day_bars(n_days=20)
    cfg = _fast_ev_config(commission_per_contract=0.0, slippage_ticks=0.0)
    strategy = EVSingleHorizonStrategy(cfg, account())

    strategy.precompute_batch({"entry": bars})

    valid = strategy._cost_hurdle.notna()
    assert valid.any()
    assert (strategy._cost_hurdle[valid] == 0.0).all()


def test_engine_end_to_end_produces_valid_trades():
    bars = make_multi_day_bars()
    cfg = _fast_ev_config()
    acct = account()

    strategy = EVSingleHorizonStrategy(cfg, acct)
    engine = BacktestEngine({"entry": bars}, strategy, acct, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) > 0
    assert all(t.setup_type == "ev_single_horizon" for t in result.trades)
    assert all(t.direction in ("long", "short") for t in result.trades)


def test_decisions_up_to_a_cutoff_are_unaffected_by_changing_bars_after_it():
    bars_a = make_multi_day_bars()
    cutoff = len(bars_a) - 60
    bars_b = bars_a.copy()
    bars_b.iloc[cutoff:, bars_b.columns.get_indexer(["open", "high", "low", "close"])] += 500.0

    cfg = _fast_ev_config()
    acct = account()

    result_a = BacktestEngine(
        {"entry": bars_a}, EVSingleHorizonStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()
    result_b = BacktestEngine(
        {"entry": bars_b}, EVSingleHorizonStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()

    assert result_a.equity_curve.iloc[: cutoff - 1].equals(result_b.equity_curve.iloc[: cutoff - 1])


# ---- dynamic_ev_exit ----


def _long_position(**overrides):
    defaults = dict(direction="long", entry_price=100.0, stop_price=95.0, target_1=110.0, contracts=1, contracts_remaining=1)
    defaults.update(overrides)
    return Position(**defaults)


def test_dynamic_ev_stop_hit_takes_priority_even_with_a_pending_exit():
    position = _long_position()
    position.strategy_state["ev_exit_pending"] = True
    bar = Bar(open=96.0, high=96.5, low=94.5, close=95.5)

    decision = dynamic_ev_exit(position, bar, current_ev=0.5)  # EV still favorable, but stop hit anyway

    assert decision.action == "stop"
    assert decision.fill_price == pytest.approx(95.0)


def test_holds_while_ev_still_favors_the_long_position():
    position = _long_position()
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.5)

    decision = dynamic_ev_exit(position, bar, current_ev=0.3)

    assert decision.action == "none"
    assert not position.strategy_state.get("ev_exit_pending")


def test_unfavorable_ev_sets_pending_but_does_not_fill_this_bar():
    position = _long_position()
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.5)

    decision = dynamic_ev_exit(position, bar, current_ev=-0.1)  # unfavorable for a long

    assert decision.action == "none"  # no fill yet -- this bar's close is already past by the time we know
    assert position.strategy_state["ev_exit_pending"] is True


def test_fills_at_the_next_bars_open_once_pending():
    position = _long_position()
    position.strategy_state["ev_exit_pending"] = True
    bar = Bar(open=102.0, high=103.0, low=101.5, close=102.5)

    decision = dynamic_ev_exit(position, bar, current_ev=-0.5)  # still unfavorable, now executing

    assert decision.action == "ev_reversal"
    assert decision.fill_price == pytest.approx(102.0)  # this bar's OPEN, not its (stale) close
    assert decision.fraction == 1.0


def test_short_position_unfavorable_ev_is_a_positive_ev():
    position = _long_position(direction="short", entry_price=100.0, stop_price=105.0, target_1=90.0)
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.5)

    decision = dynamic_ev_exit(position, bar, current_ev=0.2)  # positive EV is unfavorable for a short

    assert decision.action == "none"
    assert position.strategy_state["ev_exit_pending"] is True


def test_short_position_holds_while_ev_stays_negative():
    position = _long_position(direction="short", entry_price=100.0, stop_price=105.0, target_1=90.0)
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.5)

    decision = dynamic_ev_exit(position, bar, current_ev=-0.3)

    assert decision.action == "none"
    assert not position.strategy_state.get("ev_exit_pending")


def test_nan_ev_does_not_arm_a_pending_exit():
    position = _long_position()
    bar = Bar(open=100.0, high=101.0, low=99.0, close=100.5)

    decision = dynamic_ev_exit(position, bar, current_ev=float("nan"))

    assert decision.action == "none"
    assert not position.strategy_state.get("ev_exit_pending")


def test_engine_end_to_end_never_exits_via_full_target_in_dynamic_ev_mode():
    bars = make_multi_day_bars()
    cfg = replace(_fast_ev_config(), exit_mode="dynamic_ev")
    acct = account()

    strategy = EVSingleHorizonStrategy(cfg, acct)
    engine = BacktestEngine({"entry": bars}, strategy, acct, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) > 0
    assert all(t.exit_reason in ("stop", "ev_reversal", "session_flatten") for t in result.trades)


def test_fixed_r_and_dynamic_ev_share_the_same_first_entry():
    # Both exit modes must fire the identical *first* trade -- entries come
    # from the precomputed signal calendar, independent of exit_mode.
    bars = make_multi_day_bars()
    acct = account()

    cfg_fixed_r = _fast_ev_config()
    cfg_dynamic_ev = replace(cfg_fixed_r, exit_mode="dynamic_ev")

    result_fixed_r = BacktestEngine(
        {"entry": bars}, EVSingleHorizonStrategy(cfg_fixed_r, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()
    result_dynamic_ev = BacktestEngine(
        {"entry": bars}, EVSingleHorizonStrategy(cfg_dynamic_ev, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()

    assert len(result_fixed_r.trades) > 0 and len(result_dynamic_ev.trades) > 0
    assert result_fixed_r.trades[0].entry_time == result_dynamic_ev.trades[0].entry_time
    assert result_fixed_r.trades[0].direction == result_dynamic_ev.trades[0].direction
