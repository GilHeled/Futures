"""
Hypothesis: does trend continuation after a shallow pullback have a
statistically significant edge?

Deliberately simpler than EmaFib's pullback entry
(mnq_system/strategies/ema_fib_reversal/strategy.py): trend is a plain
EMA-slope filter (no 15m/5m bias-timeframe split), the pullback zone is a
single shallower EMA touch (no Fibonacci golden-zone), and confirmation is
just "closed further in the trend direction than the prior bar" (no
candlestick-pattern requirement). Testing the core idea in isolation from
EmaFib's specific (already-failed) mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np
import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.indicators import ema
from mnq_system.regime import ema_slope_pct
from mnq_system.strategies.hypotheses.base import (
    HypothesisExitConfig,
    HypothesisStrategy,
    exit_cfg_from_args,
)
from mnq_system.strategy_api import MarketSnapshot

__all__ = [
    "PullbackContinuationConfig", "PullbackContinuationStrategy", "DEFAULT_CONFIG",
    "add_cli_arguments", "build_from_cli_args",
]


@dataclass(frozen=True)
class PullbackContinuationConfig:
    entry_timeframe: str = "5m"
    exit: HypothesisExitConfig = field(default_factory=HypothesisExitConfig)
    trend_ema_period: int = 50
    trend_slope_lookback: int = 10
    min_trend_slope_pct: float = 0.001  # minimum |EMA slope| (% change over the lookback) to call it "trending"
    pullback_ema_period: int = 20
    pullback_touch_tolerance_atr_mult: float = 0.15


DEFAULT_CONFIG = PullbackContinuationConfig()


class PullbackContinuationStrategy(HypothesisStrategy):
    def __init__(self, cfg: PullbackContinuationConfig, account: AccountConfig):
        self.cfg = cfg
        super().__init__(
            cfg.exit, account, entry_timeframe=cfg.entry_timeframe,
            warmup_bars=max(cfg.trend_ema_period, cfg.pullback_ema_period) + cfg.trend_slope_lookback,
        )
        self.trend_slope_pct: Optional[pd.Series] = None
        self.pullback_ema: Optional[pd.Series] = None

    @property
    def name(self) -> str:
        return "pullback_continuation"

    def on_precompute(self, full_history: dict) -> None:
        close = self.bars_entry["close"]
        self.trend_slope_pct = ema_slope_pct(close, self.cfg.trend_ema_period, self.cfg.trend_slope_lookback)
        self.pullback_ema = ema(close, self.cfg.pullback_ema_period)

    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        view = snapshot.timeframes["entry"]
        j = view.pos

        slope_pct = self.trend_slope_pct.iloc[j]
        pullback_val = self.pullback_ema.iloc[j]
        if pd.isna(slope_pct) or not np.isfinite(slope_pct) or pd.isna(pullback_val):
            return None
        if abs(slope_pct) < self.cfg.min_trend_slope_pct:
            return None
        trend_dir = "long" if slope_pct > 0 else "short"

        atr_val = self.entry_atr.iloc[j]
        bar = view.bar(0)
        prev_bar = view.bar(1) if j > 0 else bar
        tolerance = self.cfg.pullback_touch_tolerance_atr_mult * atr_val
        touched_pullback = abs(bar.close - pullback_val) <= tolerance or abs(prev_bar.close - pullback_val) <= tolerance
        if not touched_pullback:
            return None

        resumed = (bar.close > prev_bar.close) if trend_dir == "long" else (bar.close < prev_bar.close)
        if not resumed:
            return None

        return trend_dir

    def build_context(self, snapshot: MarketSnapshot, atr_val: float) -> dict:
        j = snapshot.timeframes["entry"].pos
        slope_pct = self.trend_slope_pct.iloc[j]
        return {
            "trend_slope_pct": float(slope_pct) if pd.notna(slope_pct) and np.isfinite(slope_pct) else None,
            "pullback_ema": float(self.pullback_ema.iloc[j]),
        }


def add_cli_arguments(parser) -> None:
    parser.add_argument("--pullback-trend-ema-period", type=int, default=DEFAULT_CONFIG.trend_ema_period)
    parser.add_argument("--pullback-ema-period", type=int, default=DEFAULT_CONFIG.pullback_ema_period)
    parser.add_argument("--pullback-min-trend-slope-pct", type=float, default=DEFAULT_CONFIG.min_trend_slope_pct)


def build_from_cli_args(args) -> PullbackContinuationConfig:
    return replace(
        DEFAULT_CONFIG, exit=exit_cfg_from_args(args), trend_ema_period=args.pullback_trend_ema_period,
        pullback_ema_period=args.pullback_ema_period, min_trend_slope_pct=args.pullback_min_trend_slope_pct,
    )
