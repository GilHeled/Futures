from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.strategies.model_driven.weighted_combination import WeightedCombinationConfig, WeightedCombinationStrategy
from tests.strategies.model_driven._fixtures import account, fast_model_driven_config, make_multi_day_bars


def test_enters_long_when_the_composite_score_clears_the_threshold_positively():
    cfg = WeightedCombinationConfig(base=fast_model_driven_config(), composite_threshold=0.3)
    strategy = WeightedCombinationStrategy(cfg, account())

    # composite = 0.9*1 + 0.8*1 + 0.2*-1 = 1.5
    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 1, 20: 1, 40: -1}, horizon_confidences={10: 0.9, 20: 0.8, 40: 0.2}
    )

    assert signal == "long"


def test_enters_short_when_the_composite_score_clears_the_threshold_negatively():
    cfg = WeightedCombinationConfig(base=fast_model_driven_config(), composite_threshold=0.3)
    strategy = WeightedCombinationStrategy(cfg, account())

    # composite = 0.9*-1 + 0.8*-1 + 0.2*1 = -1.5
    signal = strategy.combine_horizon_signals(
        horizon_directions={10: -1, 20: -1, 40: 1}, horizon_confidences={10: 0.9, 20: 0.8, 40: 0.2}
    )

    assert signal == "short"


def test_stands_aside_when_horizons_offset_and_the_composite_does_not_clear_the_threshold():
    cfg = WeightedCombinationConfig(base=fast_model_driven_config(), composite_threshold=0.5)
    strategy = WeightedCombinationStrategy(cfg, account())

    # composite = 0.5*1 + 0.5*-1 + 0.1*1 = 0.1 -- below the 0.5 threshold
    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 1, 20: -1, 40: 1}, horizon_confidences={10: 0.5, 20: 0.5, 40: 0.1}
    )

    assert signal is None


def test_engine_end_to_end_produces_valid_trades():
    bars = make_multi_day_bars()
    cfg = WeightedCombinationConfig(base=fast_model_driven_config(), composite_threshold=0.0)
    acct = account()

    strategy = WeightedCombinationStrategy(cfg, acct)
    engine = BacktestEngine({"entry": bars}, strategy, acct, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) > 0
    assert all(t.setup_type == "model_weighted" for t in result.trades)


def test_decisions_up_to_a_cutoff_are_unaffected_by_changing_bars_after_it():
    bars_a = make_multi_day_bars()
    cutoff = len(bars_a) - 60
    bars_b = bars_a.copy()
    bars_b.iloc[cutoff:, bars_b.columns.get_indexer(["open", "high", "low", "close"])] += 500.0

    cfg = WeightedCombinationConfig(base=fast_model_driven_config(), composite_threshold=0.0)
    acct = account()

    result_a = BacktestEngine(
        {"entry": bars_a}, WeightedCombinationStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()
    result_b = BacktestEngine(
        {"entry": bars_b}, WeightedCombinationStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()

    assert result_a.equity_curve.iloc[: cutoff - 1].equals(result_b.equity_curve.iloc[: cutoff - 1])
