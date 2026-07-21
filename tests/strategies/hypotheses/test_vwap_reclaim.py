from dataclasses import replace

import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.indicators import atr, session_vwap
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig
from mnq_system.strategies.hypotheses.vwap_reclaim import VwapReclaimConfig, VwapReclaimStrategy
from mnq_system.strategy_api import MarketSnapshot, TimeframeView


def _account():
    return replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), timezone="America/New_York"),
    )


def _make_bars(bar_tuples, start="2026-06-01 09:30", freq="5min", tz="America/New_York"):
    idx = pd.date_range(start, periods=len(bar_tuples), freq=freq, tz=tz)
    return pd.DataFrame(
        {
            "open": [b[0] for b in bar_tuples], "high": [b[1] for b in bar_tuples],
            "low": [b[2] for b in bar_tuples], "close": [b[3] for b in bar_tuples],
            "volume": [1000] * len(bar_tuples),
        },
        index=idx,
    )


def _snapshot(entry_bars, j):
    return MarketSnapshot(timeframes={"entry": TimeframeView(entry_bars, j)}, equity=50_000.0)


def _run_through(strategy, entry_bars, upto_idx):
    for j in range(upto_idx + 1):
        strategy.on_bar(_snapshot(entry_bars, j))


def _build_extended_above_then_touch_bars(cfg):
    # Flat baseline near 100 (settles VWAP/ATR), then a sustained jump to
    # 130 that keeps price "extended" above VWAP for a few bars, then a
    # touch bar whose close is set exactly to the computed VWAP at that
    # point -- guarantees the touch condition regardless of hand-derived
    # VWAP/ATR arithmetic.
    baseline = [(100.0, 100.5, 99.5, 100.0)] * 5
    jump = [(100.0, 131.0, 99.0, 130.0), (130.0, 131.0, 129.0, 130.0), (130.0, 131.0, 129.0, 130.0)]
    partial = _make_bars(baseline + jump)
    vwap_series = session_vwap(partial, tz="America/New_York")
    atr_series = atr(partial, period=cfg.exit.atr_period)
    touch_close = float(vwap_series.iloc[-1])
    touch_bar = (130.0, 130.5, touch_close - 0.1, touch_close)
    all_bars = _make_bars(baseline + jump + [touch_bar])
    return all_bars, len(baseline) + len(jump)


def test_extended_above_vwap_then_touch_fires_long_bounce():
    cfg = VwapReclaimConfig(exit=HypothesisExitConfig(atr_period=3), extension_atr_mult=1.0, touch_tolerance_atr_mult=0.5)
    account = _account()
    entry_bars, j = _build_extended_above_then_touch_bars(cfg)

    strategy = VwapReclaimStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is not None
    assert signal.direction == "long"
    assert signal.setup_type == "vwap_reclaim"
    assert "vwap" in signal.context


def test_no_extension_no_signal():
    cfg = VwapReclaimConfig(exit=HypothesisExitConfig(atr_period=3), extension_atr_mult=1.0, touch_tolerance_atr_mult=0.5)
    account = _account()
    # Price stays near VWAP the whole time -- never becomes "extended".
    quiet_bars = [(100.0, 100.5, 99.5, 100.0)] * 8
    entry_bars = _make_bars(quiet_bars)
    j = len(quiet_bars) - 1

    strategy = VwapReclaimStrategy(cfg, account)
    strategy.precompute_batch({"entry": entry_bars})
    _run_through(strategy, entry_bars, j)
    signal = strategy.check_entry(_snapshot(entry_bars, j))

    assert signal is None


def test_vwap_reclaim_engine_end_to_end():
    cfg = VwapReclaimConfig(exit=HypothesisExitConfig(atr_period=3))
    account = _account()
    entry_bars, j = _build_extended_above_then_touch_bars(cfg)
    exit_bar = (entry_bars["close"].iloc[-1], entry_bars["close"].iloc[-1] + 20.0, entry_bars["close"].iloc[-1] - 1.0, entry_bars["close"].iloc[-1] + 18.0)
    idx = entry_bars.index.append(pd.date_range(entry_bars.index[-1] + pd.Timedelta(minutes=5), periods=1, freq="5min"))
    full_bars = pd.concat(
        [entry_bars, pd.DataFrame([{"open": exit_bar[0], "high": exit_bar[1], "low": exit_bar[2], "close": exit_bar[3], "volume": 1000}], index=[idx[-1]])]
    )

    strategy = VwapReclaimStrategy(cfg, account)
    engine = BacktestEngine({"entry": full_bars}, strategy, account, BacktestSettings(account_equity=50_000.0))
    result = engine.run()

    assert len(result.trades) == 1
    assert result.trades[0].direction == "long"
    assert result.trades[0].setup_type == "vwap_reclaim"
