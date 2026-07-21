from dataclasses import replace

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.strategies.model_driven.full_agreement import FullAgreementStrategy
from tests.strategies.model_driven._fixtures import account, fast_model_driven_config, make_multi_day_bars


def test_enters_long_when_all_horizons_agree_up_and_clear_threshold():
    cfg = fast_model_driven_config(confidence_threshold=0.5)
    strategy = FullAgreementStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 1, 20: 1, 40: 1}, horizon_confidences={10: 0.6, 20: 0.7, 40: 0.8}
    )

    assert signal == "long"


def test_enters_short_when_all_horizons_agree_down_and_clear_threshold():
    cfg = fast_model_driven_config(confidence_threshold=0.5)
    strategy = FullAgreementStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: -1, 20: -1, 40: -1}, horizon_confidences={10: 0.6, 20: 0.7, 40: 0.8}
    )

    assert signal == "short"


def test_stands_aside_on_disagreement():
    cfg = fast_model_driven_config(confidence_threshold=0.0)
    strategy = FullAgreementStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 1, 20: -1, 40: 1}, horizon_confidences={10: 0.9, 20: 0.9, 40: 0.9}
    )

    assert signal is None


def test_stands_aside_when_all_horizons_agree_but_on_flat():
    cfg = fast_model_driven_config(confidence_threshold=0.0)
    strategy = FullAgreementStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 0, 20: 0, 40: 0}, horizon_confidences={10: 0.9, 20: 0.9, 40: 0.9}
    )

    assert signal is None


def test_stands_aside_when_one_horizon_fails_the_shared_threshold():
    cfg = fast_model_driven_config(confidence_threshold=0.9)
    strategy = FullAgreementStrategy(cfg, account())

    signal = strategy.combine_horizon_signals(
        horizon_directions={10: 1, 20: 1, 40: 1}, horizon_confidences={10: 0.95, 20: 0.95, 40: 0.5}
    )

    assert signal is None  # h40's confidence (0.5) doesn't clear the shared threshold (0.9)


def test_engine_end_to_end_produces_valid_full_agreement_trades():
    bars = make_multi_day_bars()
    cfg = fast_model_driven_config()
    acct = account()

    strategy = FullAgreementStrategy(cfg, acct)
    engine = BacktestEngine({"entry": bars}, strategy, acct, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) > 0
    assert all(t.setup_type == "model_full_agreement" for t in result.trades)
    assert all(t.direction in ("long", "short") for t in result.trades)


def test_decisions_up_to_a_cutoff_are_unaffected_by_changing_bars_after_it():
    bars_a = make_multi_day_bars()
    cutoff = len(bars_a) - 60  # well inside the tail, after OOS coverage has begun
    bars_b = bars_a.copy()
    bars_b.iloc[cutoff:, bars_b.columns.get_indexer(["open", "high", "low", "close"])] += 500.0  # wildly different tail

    cfg = fast_model_driven_config()
    acct = account()

    result_a = BacktestEngine({"entry": bars_a}, FullAgreementStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)).run()
    result_b = BacktestEngine({"entry": bars_b}, FullAgreementStrategy(cfg, acct), acct, BacktestSettings(account_equity=50_000.0)).run()

    equity_a_before_cutoff = result_a.equity_curve.iloc[: cutoff - 1]
    equity_b_before_cutoff = result_b.equity_curve.iloc[: cutoff - 1]
    assert equity_a_before_cutoff.equals(equity_b_before_cutoff)
