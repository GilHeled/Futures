from dataclasses import replace

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.indicators import ema
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig
from mnq_system.strategies.hypotheses.pullback_continuation import (
    PullbackContinuationConfig,
    PullbackContinuationStrategy,
)
from mnq_system.strategy_api import MarketSnapshot, TimeframeView

_FAST_CFG_KWARGS = dict(trend_ema_period=5, pullback_ema_period=3, trend_slope_lookback=2, min_trend_slope_pct=0.001)


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), timezone="America/New_York"),
    )


def _make_bars(closes, opens=None, highs=None, lows=None, start="2026-06-01 09:00", freq="5min", tz="America/New_York"):
    n = len(closes)
    opens = opens or closes
    highs = highs or [c + 0.5 for c in closes]
    lows = lows or [c - 0.5 for c in closes]
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes, "volume": [1000] * n}, index=idx)


def _snapshot(entry_bars, j):
    return MarketSnapshot(timeframes={"entry": TimeframeView(entry_bars, j)}, equity=50_000.0)


def _uptrend_then_pullback_and_resume_closes():
    # 12-bar steady uptrend, then an exact pullback-EMA touch bar (close set
    # algebraically -- see comment below -- rather than hand-derived), then
    # a resume bar closing higher.
    baseline = [100.0 + 2.0 * i for i in range(12)]
    pullback_ema_period = _FAST_CFG_KWARGS["pullback_ema_period"]
    partial_ema = ema(pd.Series(baseline), period=pullback_ema_period)
    # If close_touch == ema.iloc[-1] (the EMA value right before it), the new
    # EMA value after including close_touch is algebraically identical to
    # close_touch itself: ema_new = alpha*close_touch + (1-alpha)*ema_prior
    # == close_touch when close_touch == ema_prior. Guarantees an exact
    # "touch" with no hand-derived arithmetic.
    touch_close = float(partial_ema.iloc[-1])
    resume_close = touch_close + 0.4  # small enough that the touch-bar EMA (which shifts once this bar is included) stays within tolerance
    return baseline + [touch_close, resume_close]


def test_uptrend_pullback_touch_and_resume_fires_long():
    cfg = PullbackContinuationConfig(exit=HypothesisExitConfig(atr_period=3), **_FAST_CFG_KWARGS)
    account = _account()
    closes = _uptrend_then_pullback_and_resume_closes()
    entry_bars = _make_bars(closes)
    j = len(closes) - 1

    strategy = PullbackContinuationStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.setup_type == "pullback_continuation"
    assert signal.context["trend_slope_pct"] > 0


def test_no_signal_when_trend_is_flat():
    cfg = PullbackContinuationConfig(exit=HypothesisExitConfig(atr_period=3), **_FAST_CFG_KWARGS)
    account = _account()
    closes = [100.0] * 14  # perfectly flat -- no trend slope at all
    entry_bars = _make_bars(closes)
    j = len(closes) - 1

    strategy = PullbackContinuationStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_no_signal_when_price_never_pulled_back_to_the_ema():
    cfg = PullbackContinuationConfig(exit=HypothesisExitConfig(atr_period=3), **_FAST_CFG_KWARGS)
    account = _account()
    # A steady climb (note: a perfectly *constant*-step ramp isn't a valid
    # "no touch" fixture -- EMA's steady-state lag behind a fixed-step ramp
    # converges to exactly one bar's worth of steps, i.e. the PREVIOUS bar's
    # close ends up sitting right on the EMA regardless of how steep the
    # ramp is) followed by two large one-off jumps far outrunning the EMA,
    # so neither the current nor the previous bar is within tolerance of it.
    baseline = [100.0 + 2.0 * i for i in range(12)]
    jump_1 = baseline[-1] + 30.0
    jump_2 = jump_1 + 30.0
    closes = baseline + [jump_1, jump_2]
    entry_bars = _make_bars(closes)
    j = len(closes) - 1

    strategy = PullbackContinuationStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_pullback_continuation_engine_end_to_end():
    # A continuous uptrend can legitimately offer more than one qualifying
    # touch-and-resume moment (this test only proves the engine wiring is
    # correct, not that exactly one signal fires -- unlike the other
    # hypotheses' discrete, one-shot events).
    cfg = PullbackContinuationConfig(exit=HypothesisExitConfig(atr_period=3), **_FAST_CFG_KWARGS)
    account = _account()
    base_closes = _uptrend_then_pullback_and_resume_closes()
    exit_close = base_closes[-1] + 20.0  # comfortably clears the long's target
    closes = base_closes + [exit_close]
    highs = [c + 0.5 for c in base_closes] + [exit_close + 1.0]
    entry_bars = _make_bars(closes, highs=highs)

    strategy = PullbackContinuationStrategy(cfg, account)
    engine = BacktestEngine({"entry": entry_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) >= 1
    assert result.trades[0].direction == "long"
    assert result.trades[0].setup_type == "pullback_continuation"
