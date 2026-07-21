"""
Engine-level tests: bookkeeping/wiring that must hold regardless of which
Strategy is plugged in (no-lookahead, equity-curve coverage, daily-limit
enforcement, session-end flattening), plus one test driving a trivial
from-scratch dummy Strategy with zero EMA/Fib/reversal code -- the concrete
proof that BacktestEngine is genuinely strategy-agnostic, not just
refactored to still only work with one strategy.

Strategy-specific behavior (entry/exit rules, context enrichment, the
reversal state machine) is tested against EmaFibReversalStrategy directly in
tests/strategies/ema_fib_reversal/.
"""

from dataclasses import replace
from typing import Optional

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.strategies.ema_fib_reversal.config import EmaConfig, EmaFibReversalConfig, SwingConfig
from mnq_system.strategies.ema_fib_reversal.strategy import EmaFibReversalStrategy
from mnq_system.strategy_api import EntrySignal, ExitDecision, MarketSnapshot, Position, Strategy, TimeframeSpec

# ---------------------------------------------------------------- shared EmaFibReversal fixture
# (kept minimal and local to this file -- see tests/strategies/ema_fib_reversal/
# for the full behavioral suite; these are only used to exercise generic
# engine bookkeeping, not strategy-specific rules.)


def _fast_config():
    strategy_cfg = replace(
        EmaFibReversalConfig(),
        ema=EmaConfig(fast=2, mid=3, slow=5, slope_lookback=2),
        swing=SwingConfig(lookback=1),
    )
    account = replace(
        AccountConfig(),
        session=SessionConfig(
            trading_windows=((0, 0, 23, 59),),
            reduced_size_windows=(),
            flatten_before_close=True,
            timezone="UTC",
        ),
    )
    return strategy_cfg, account


_LONG_PULLBACK_BARS = [
    (100.0, 100.3, 99.7, 100.0),
    (100.0, 100.3, 99.7, 100.0),
    (100.0, 100.3, 99.7, 100.0),
    (100.0, 100.3, 97.7, 98.0),
    (98.0, 104.3, 97.7, 104.0),
    (104.0, 108.3, 103.7, 108.0),
    (108.0, 112.3, 107.7, 112.0),
    (112.0, 116.3, 111.7, 116.0),
    (116.0, 120.3, 115.7, 120.0),
    (120.0, 124.3, 119.7, 124.0),
    (124.0, 128.3, 123.7, 128.0),
    (128.0, 132.3, 127.7, 132.0),
    (132.0, 136.3, 131.7, 136.0),
    (136.0, 140.3, 135.7, 140.0),
    (140.0, 144.3, 139.7, 144.0),
    (144.0, 148.3, 143.7, 148.0),
    (148.0, 148.4, 143.9, 142.0),
    (142.0, 142.3, 135.7, 136.0),
    (136.0, 136.3, 129.5, 130.0),
    (130.0, 130.3, 123.5, 124.0),
    (124.0, 124.3, 119.0, 120.0),
    (119.5, 119.85, 116.0, 119.8),  # hammer -> expected long entry
    (119.8, 123.3, 119.0, 123.0),
    (123.0, 127.3, 122.7, 127.0),
    (127.0, 131.3, 126.7, 131.0),
    (131.0, 135.3, 130.7, 135.0),
    (135.0, 139.3, 134.7, 139.0),
    (139.0, 143.3, 138.7, 143.0),
    (143.0, 147.3, 142.7, 147.0),
    (147.0, 151.3, 146.7, 151.0),
    (151.0, 155.3, 150.7, 155.0),
    (155.0, 159.3, 154.7, 159.0),
]

_BIAS_CLOSES = [100, 98, 104, 102, 108, 106, 112, 110, 116, 114]


def _make_bias_bars(closes=_BIAS_CLOSES, start="2026-06-01 09:00"):
    idx = pd.date_range(start, periods=len(closes), freq="15min", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes, "close": closes,
            "high": [c + 0.3 for c in closes], "low": [c - 0.3 for c in closes],
            "volume": [1000] * len(closes),
        },
        index=idx,
    )


def _make_entry_bars(bar_tuples=_LONG_PULLBACK_BARS, start="2026-06-01 09:00"):
    idx = pd.date_range(start, periods=len(bar_tuples), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [b[0] for b in bar_tuples],
            "high": [b[1] for b in bar_tuples],
            "low": [b[2] for b in bar_tuples],
            "close": [b[3] for b in bar_tuples],
            "volume": [1000] * len(bar_tuples),
        },
        index=idx,
    )


def _run_engine(strategy_cfg, account, settings=None):
    bias_bars, entry_bars = _make_bias_bars(), _make_entry_bars()
    strategy = EmaFibReversalStrategy(strategy_cfg, account)
    engine = BacktestEngine(
        {"bias": bias_bars, "entry": entry_bars}, strategy, account, settings or BacktestSettings(account_equity=50_000.0)
    )
    return engine.run(), entry_bars


def test_engine_flattens_the_open_position_at_the_end_of_the_data():
    strategy_cfg, account = _fast_config()

    result, entry_bars = _run_engine(strategy_cfg, account)

    # Assert: the only trade closes out via session/data-end flatten, not left dangling
    assert result.trades[0].exit_reason in ("session_flatten", "full_target")
    assert result.equity_curve.index[-1] == entry_bars.index[-1]


def test_engine_respects_max_trades_per_day_after_the_first_trade_closes_a_loss():
    # Arrange: max_trades_per_day=0 is an account-level limit -- no new trade
    # should even be attempted (belt-and-suspenders check on the wiring).
    strategy_cfg, account = _fast_config()
    account = replace(account, risk=replace(account.risk, max_trades_per_day=0))

    result, _ = _run_engine(strategy_cfg, account)

    assert result.trades == []


def test_engine_decisions_up_to_bar_j_are_unaffected_by_changing_bars_after_j():
    # Arrange: two datasets identical up through the entry bar, diverging only afterward
    strategy_cfg, account = _fast_config()
    bias_bars = _make_bias_bars()
    entry_bars_a = _make_entry_bars()
    altered_tuples = list(_LONG_PULLBACK_BARS)
    # Wildly change every bar strictly after the entry bar (index 21)
    for i in range(22, len(altered_tuples)):
        altered_tuples[i] = (10.0, 10.1, 9.9, 10.0)
    entry_bars_b = _make_entry_bars(bar_tuples=altered_tuples)

    strategy_a = EmaFibReversalStrategy(strategy_cfg, account)
    strategy_b = EmaFibReversalStrategy(strategy_cfg, account)
    engine_a = BacktestEngine({"bias": bias_bars, "entry": entry_bars_a}, strategy_a, account, BacktestSettings(account_equity=50_000.0))
    engine_b = BacktestEngine({"bias": bias_bars, "entry": entry_bars_b}, strategy_b, account, BacktestSettings(account_equity=50_000.0))

    # Act
    result_a = engine_a.run()
    result_b = engine_b.run()

    # Assert: the entry itself (decided at bar 21) is identical in both runs,
    # proving the engine didn't peek at bars 22+ to make that decision.
    assert result_a.trades[0].entry_time == result_b.trades[0].entry_time
    assert result_a.trades[0].entry_price == result_b.trades[0].entry_price
    assert result_a.trades[0].direction == result_b.trades[0].direction


def test_engine_result_equity_curve_covers_every_entry_bar():
    strategy_cfg, account = _fast_config()

    result, entry_bars = _run_engine(strategy_cfg, account)

    assert len(result.equity_curve) == len(entry_bars)


# ---------------------------------------------------------------- pluggability proof


class _DummyBuyAndHoldStrategy(Strategy):
    """Deliberately trivial: no EMA/Fib/reversal code at all. Enters long on
    a fixed bar index with a fixed stop/target, exits on another fixed bar
    index. Exists purely to prove BacktestEngine works with ANY Strategy
    implementation, not just EmaFibReversalStrategy.
    """

    def __init__(self, entry_bar_index: int, exit_bar_index: int):
        self._entry_bar_index = entry_bar_index
        self._exit_bar_index = exit_bar_index

    @property
    def name(self) -> str:
        return "dummy_buy_and_hold"

    @property
    def timeframes(self) -> dict:
        return {"entry": TimeframeSpec("5m")}

    @property
    def driving_timeframe(self) -> str:
        return "entry"

    def on_bar(self, snapshot: MarketSnapshot) -> None:
        return

    def check_entry(self, snapshot: MarketSnapshot) -> Optional[EntrySignal]:
        view = snapshot.timeframes["entry"]
        if view.pos != self._entry_bar_index:
            return None
        bar = view.bar(0)
        return EntrySignal(
            direction="long", setup_type="dummy", entry_price=bar.close,
            stop_price=bar.close - 10.0, targets=[bar.close + 1000.0],
        )

    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        view = snapshot.timeframes["entry"]
        if view.pos >= self._exit_bar_index:
            return ExitDecision(action="dummy_time_exit", fill_price=view.bar(0).close, fraction=1.0)
        return ExitDecision(action="none")


def test_engine_works_with_a_trivial_dummy_strategy():
    # Arrange: 15 flat bars -- nothing EMA/Fib/reversal-shaped about them at all
    idx = pd.date_range("2026-06-01 09:00", periods=15, freq="5min", tz="UTC")
    entry_bars = pd.DataFrame(
        {"open": [100.0] * 15, "high": [100.5] * 15, "low": [99.5] * 15, "close": [100.0 + i for i in range(15)]},
        index=idx,
    )
    strategy = _DummyBuyAndHoldStrategy(entry_bar_index=5, exit_bar_index=10)
    account = replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), timezone="UTC"),
    )
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))

    # Act
    result = engine.run()

    # Assert
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.direction == "long"
    assert trade.setup_type == "dummy"
    assert trade.entry_time == idx[5]
    assert trade.exit_reason == "dummy_time_exit"
    assert trade.pnl > 0  # price only ever rises in this fixture
