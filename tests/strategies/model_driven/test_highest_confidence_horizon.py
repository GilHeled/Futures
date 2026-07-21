from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.strategies.model_driven.highest_confidence_horizon import HighestConfidenceHorizonStrategy
from tests.strategies.model_driven._fixtures import account, fast_model_driven_config, make_multi_day_bars


def test_trades_the_direction_of_whichever_horizon_has_the_highest_confidence():
    cfg = fast_model_driven_config(confidence_threshold=0.0)
    strategy = HighestConfidenceHorizonStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 1, 20: -1, 40: -1}, horizon_confidences={10: 0.9, 20: 0.5, 40: 0.4}
    )

    assert signal == "long"  # horizon 10 has the highest confidence (0.9), disagreement elsewhere is ignored


def test_stands_aside_when_the_highest_confidence_horizon_is_flat():
    cfg = fast_model_driven_config(confidence_threshold=0.0)
    strategy = HighestConfidenceHorizonStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 0, 20: -1, 40: 1}, horizon_confidences={10: 0.9, 20: 0.5, 40: 0.4}
    )

    assert signal is None


def test_stands_aside_when_the_highest_confidence_horizon_fails_the_shared_threshold():
    cfg = fast_model_driven_config(confidence_threshold=0.95)
    strategy = HighestConfidenceHorizonStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 1, 20: -1, 40: -1}, horizon_confidences={10: 0.9, 20: 0.5, 40: 0.4}
    )

    assert signal is None  # h10 wins on confidence but 0.9 < the shared 0.95 bar


def test_engine_end_to_end_produces_valid_trades():
    bars = make_multi_day_bars()
    cfg = fast_model_driven_config()
    acct = account()

    strategy = HighestConfidenceHorizonStrategy(cfg, acct)
    engine = BacktestEngine({"entry": bars}, strategy, acct, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) > 0
    assert all(t.setup_type == "model_highest_confidence" for t in result.trades)


def test_decisions_up_to_a_cutoff_are_unaffected_by_changing_bars_after_it():
    bars_a = make_multi_day_bars()
    cutoff = len(bars_a) - 60
    bars_b = bars_a.copy()
    bars_b.iloc[cutoff:, bars_b.columns.get_indexer(["open", "high", "low", "close"])] += 500.0

    cfg = fast_model_driven_config()
    acct = account()

    result_a = BacktestEngine(
        {"entry": bars_a}, HighestConfidenceHorizonStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()
    result_b = BacktestEngine(
        {"entry": bars_b}, HighestConfidenceHorizonStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)
    ).run()

    assert result_a.equity_curve.iloc[: cutoff - 1].equals(result_b.equity_curve.iloc[: cutoff - 1])
