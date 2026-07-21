"""
Hypothesis: does a liquidity sweep -- price briefly piercing a recent
confirmed swing high/low, then closing back on the other side -- create a
measurable directional edge in the opposite direction of the sweep?

Deliberately simpler than EmaFib's ReversalSetup
(mnq_system/strategies/ema_fib_reversal/strategy.py): no bias filter, no
candlestick-pattern confirmation, no multi-bar retest window -- just
"swept and immediately rejected, same bar," to test that idea in
isolation from EmaFib's specific (already-failed) mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.strategies.hypotheses.base import (
    HypothesisExitConfig,
    HypothesisStrategy,
    add_shared_exit_cli_arguments,
    exit_cfg_from_args,
)
from mnq_system.strategy_api import MarketSnapshot
from mnq_system.swings import compute_swings, latest_confirmed_swing

__all__ = ["LiquiditySweepConfig", "LiquiditySweepStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args"]


@dataclass(frozen=True)
class LiquiditySweepConfig:
    entry_timeframe: str = "5m"
    exit: HypothesisExitConfig = field(default_factory=HypothesisExitConfig)
    swing_lookback: int = 2  # fractal window -- same convention as mnq_system/swings.py elsewhere


DEFAULT_CONFIG = LiquiditySweepConfig()


class LiquiditySweepStrategy(HypothesisStrategy):
    def __init__(self, cfg: LiquiditySweepConfig, account: AccountConfig):
        self.cfg = cfg
        super().__init__(
            cfg.exit, account, entry_timeframe=cfg.entry_timeframe, warmup_bars=2 * cfg.swing_lookback + 5
        )
        self.entry_swings: Optional[pd.DataFrame] = None

    @property
    def name(self) -> str:
        return "liquidity_sweep"

    def on_precompute(self, full_history: dict) -> None:
        self.entry_swings = compute_swings(self.bars_entry, lookback=self.cfg.swing_lookback)

    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        view = snapshot.timeframes["entry"]
        j = view.pos
        if j == 0:
            return None
        bar = view.bar(0)

        swing_high = latest_confirmed_swing(self.bars_entry, self.entry_swings, j, self.cfg.swing_lookback, "high")
        if swing_high is not None and bar.high > swing_high.price and bar.close < swing_high.price:
            return "short"  # swept above, rejected back below -> bet reversal down

        swing_low = latest_confirmed_swing(self.bars_entry, self.entry_swings, j, self.cfg.swing_lookback, "low")
        if swing_low is not None and bar.low < swing_low.price and bar.close > swing_low.price:
            return "long"  # swept below, rejected back above -> bet reversal up

        return None


def add_cli_arguments(parser) -> None:
    """Registers the shared --hyp-* flags (see add_shared_exit_cli_arguments)
    plus this hypothesis's own --sweep-swing-lookback. This is the one
    hypothesis spec designated to own the shared flags -- see
    mnq_system/strategies/__init__.py.
    """
    add_shared_exit_cli_arguments(parser)
    parser.add_argument("--sweep-swing-lookback", type=int, default=DEFAULT_CONFIG.swing_lookback)


def build_from_cli_args(args) -> LiquiditySweepConfig:
    return replace(DEFAULT_CONFIG, exit=exit_cfg_from_args(args), swing_lookback=args.sweep_swing_lookback)
