"""
Verifies the four properties the replay layer exists to prove:
1. Replay-mode decisions match BacktestEngine.run() exactly (same step()).
2. The strategy behaves causally under replay -- no lookahead leakage.
3. Every entry/exit recommendation is logged with disposition and context.
4. Only bars with an actual signal are logged (not every bar).
"""

from dataclasses import replace

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig
from mnq_system.replay import run_replay
from mnq_system.strategies.ema_fib_reversal.strategy import EmaFibReversalStrategy
from tests.test_backtest_engine import (
    _LONG_PULLBACK_BARS,
    _fast_config,
    _make_bias_bars,
    _make_entry_bars,
)


def test_replay_matches_backtest_exactly():
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    settings = BacktestSettings(account_equity=50_000.0)

    backtest_engine = BacktestEngine(
        {"bias": bias_bars, "entry": entry_bars}, EmaFibReversalStrategy(strategy_cfg, account), account, settings
    )
    backtest_result = backtest_engine.run()

    replay_result = run_replay(
        {"bias": bias_bars, "entry": entry_bars}, EmaFibReversalStrategy(strategy_cfg, account), account, settings
    )

    assert len(replay_result.trades) == len(backtest_result.trades)
    for replay_trade, backtest_trade in zip(replay_result.trades, backtest_result.trades):
        assert replay_trade.entry_time == backtest_trade.entry_time
        assert replay_trade.exit_time == backtest_trade.exit_time
        assert replay_trade.entry_price == backtest_trade.entry_price
        assert replay_trade.exit_price == backtest_trade.exit_price
        assert replay_trade.pnl == backtest_trade.pnl
    assert replay_result.final_equity == backtest_result.final_equity
    assert list(replay_result.equity_curve) == list(backtest_result.equity_curve)


def test_replay_decisions_up_to_bar_j_are_unaffected_by_changing_bars_after_j():
    # Same no-lookahead fixture as tests/test_backtest_engine.py, driven
    # through run_replay instead of a raw BacktestEngine -- proves the
    # audit-log hook itself introduces no lookahead either.
    strategy_cfg, account = _fast_config()
    bias_bars = _make_bias_bars()
    entry_bars_a = _make_entry_bars()
    altered_tuples = list(_LONG_PULLBACK_BARS)
    for i in range(22, len(altered_tuples)):
        altered_tuples[i] = (10.0, 10.1, 9.9, 10.0)
    entry_bars_b = _make_entry_bars(bar_tuples=altered_tuples)

    result_a = run_replay(
        {"bias": bias_bars, "entry": entry_bars_a}, EmaFibReversalStrategy(strategy_cfg, account), account,
        BacktestSettings(account_equity=50_000.0),
    )
    result_b = run_replay(
        {"bias": bias_bars, "entry": entry_bars_b}, EmaFibReversalStrategy(strategy_cfg, account), account,
        BacktestSettings(account_equity=50_000.0),
    )

    assert result_a.trades[0].entry_time == result_b.trades[0].entry_time
    assert result_a.trades[0].entry_price == result_b.trades[0].entry_price
    assert result_a.trades[0].direction == result_b.trades[0].direction


def test_replay_audit_log_records_an_accepted_entry_with_full_context():
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()

    result = run_replay(
        {"bias": bias_bars, "entry": entry_bars}, EmaFibReversalStrategy(strategy_cfg, account), account,
        BacktestSettings(account_equity=50_000.0), symbol="MNQ",
    )

    entry_signals = [e for e in result.audit_log if e.signal_type == "entry" and e.disposition == "accepted"]
    assert len(entry_signals) == len(result.trades)
    entry = entry_signals[0]
    assert entry.symbol == "MNQ"
    assert entry.strategy_name == "ema_fib_reversal"
    assert entry.timeframe == "5m"
    assert entry.direction in ("long", "short")
    assert entry.stop_price is not None
    assert entry.targets
    assert entry.risk_dollars is not None and entry.risk_dollars > 0
    assert entry.contracts is not None and entry.contracts > 0
    assert {"bias", "volatility_regime", "trend_regime"} <= entry.context.keys()


def test_replay_audit_log_records_blocked_entry_when_sizing_yields_zero_contracts():
    strategy_cfg, account = _fast_config()
    account = replace(account, risk=replace(account.risk, risk_pct_per_trade=1e-9))
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()

    result = run_replay(
        {"bias": bias_bars, "entry": entry_bars}, EmaFibReversalStrategy(strategy_cfg, account), account,
        BacktestSettings(account_equity=50_000.0),
    )

    assert result.trades == []
    blocked = [e for e in result.audit_log if e.disposition == "blocked_sizing_zero"]
    assert len(blocked) >= 1
    assert blocked[0].signal_type == "entry"
    assert blocked[0].contracts is None
    assert blocked[0].risk_dollars is None


def test_replay_audit_log_records_exits_matching_trade_exit_reason():
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()

    result = run_replay(
        {"bias": bias_bars, "entry": entry_bars}, EmaFibReversalStrategy(strategy_cfg, account), account,
        BacktestSettings(account_equity=50_000.0),
    )

    exit_signals = [e for e in result.audit_log if e.signal_type == "exit"]
    assert len(exit_signals) == len(result.trades)
    assert exit_signals[0].reason == result.trades[0].exit_reason


def test_replay_audit_log_only_records_bars_with_actual_signals():
    strategy_cfg, account = _fast_config()
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()

    result = run_replay(
        {"bias": bias_bars, "entry": entry_bars}, EmaFibReversalStrategy(strategy_cfg, account), account,
        BacktestSettings(account_equity=50_000.0),
    )

    # Far fewer audit entries than driving bars -- most bars are flat/no-op
    # and must not produce a log row (only actual entry/exit signals do).
    assert 0 < len(result.audit_log) < len(entry_bars)
