"""
Shared base for atomic market-hypothesis tests (the "alpha discovery"
research process: validate one small market hypothesis at a time before
combining survivors into a complete strategy). A concrete hypothesis
implements only `detect_event` (plus whatever state-tracking its own event
needs) -- entry sizing (ATR-based stop, fixed R-multiple target) and exit
are standardized here so every hypothesis is directly comparable to every
other hypothesis, to ORBStrategy, and to the benchmark_* naive baselines on
the same avg-R/profit-factor scale.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.indicators import atr
from mnq_system.strategies.common import simple_stop_target_exit
from mnq_system.strategy_api import EntrySignal, ExitDecision, MarketSnapshot, Position, Strategy, TimeframeSpec


@dataclass(frozen=True)
class HypothesisExitConfig:
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    target_r_multiple: float = 1.5


def add_shared_exit_cli_arguments(parser) -> None:
    """Registers the --hyp-* flags shared by every hypothesis registry
    entry. Only ONE hypothesis StrategySpec should reference this (see
    mnq_system/strategies/__init__.py) -- every registered strategy's
    add_cli_arguments runs against the same shared parser, and argparse
    errors on a flag being added twice. The other hypothesis specs use
    mnq_system.strategies.common.noop_add_cli_arguments instead.
    """
    parser.add_argument("--hyp-atr-period", type=int, default=HypothesisExitConfig().atr_period)
    parser.add_argument("--hyp-stop-atr-mult", type=float, default=HypothesisExitConfig().stop_atr_mult)
    parser.add_argument("--hyp-target-r", type=float, default=HypothesisExitConfig().target_r_multiple)


def exit_cfg_from_args(args) -> HypothesisExitConfig:
    return HypothesisExitConfig(
        atr_period=args.hyp_atr_period, stop_atr_mult=args.hyp_stop_atr_mult, target_r_multiple=args.hyp_target_r
    )


class HypothesisStrategy(Strategy):
    """Base class every hypothesis subclasses. `timeframes`, `on_bar`,
    `check_entry`, `check_exit`, and `diagnostic_dimensions` are standardized
    here and not meant to be overridden -- a subclass's only required work
    is `detect_event`; `on_precompute`/`on_event_bar`/`build_context` are
    optional hooks for whatever state its event needs.
    """

    def __init__(
        self,
        exit_cfg: HypothesisExitConfig,
        account: AccountConfig,
        entry_timeframe: str = "5m",
        warmup_bars: int = 0,
    ):
        self.exit_cfg = exit_cfg
        self.account = account
        self.timezone = account.session.timezone
        self._entry_timeframe = entry_timeframe
        self._warmup_bars = max(warmup_bars, exit_cfg.atr_period)

        # Populated by precompute_batch()
        self.bars_entry: Optional[pd.DataFrame] = None
        self.entry_atr: Optional[pd.Series] = None

    # ---- Strategy interface: standardized, not overridden by subclasses ----

    @property
    def timeframes(self) -> dict:
        return {"entry": TimeframeSpec(self._entry_timeframe, warmup_bars=self._warmup_bars)}

    @property
    def driving_timeframe(self) -> str:
        return "entry"

    def precompute_batch(self, full_history: dict) -> None:
        self.bars_entry = full_history["entry"]
        self.entry_atr = atr(self.bars_entry, period=self.exit_cfg.atr_period)
        self.on_precompute(full_history)

    def on_bar(self, snapshot: MarketSnapshot) -> None:
        self.on_event_bar(snapshot)

    def check_entry(self, snapshot: MarketSnapshot) -> Optional[EntrySignal]:
        entry_view = snapshot.timeframes["entry"]
        j = entry_view.pos
        atr_val = self.entry_atr.iloc[j]
        if pd.isna(atr_val) or atr_val <= 0:
            return None  # ATR not warmed up yet

        direction = self.detect_event(snapshot)
        if direction is None:
            return None

        bar = entry_view.bar(0)
        entry_price = bar.close
        sign = 1 if direction == "long" else -1
        stop = entry_price - self.exit_cfg.stop_atr_mult * atr_val * sign
        risk = abs(entry_price - stop)
        if risk <= 0:
            return None
        target = entry_price + risk * self.exit_cfg.target_r_multiple * sign

        context = {"atr": float(atr_val), **self.build_context(snapshot, atr_val)}
        return EntrySignal(
            direction=direction, setup_type=self.name, entry_price=entry_price, stop_price=stop,
            targets=[target], context=context,
        )

    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        return simple_stop_target_exit(position, snapshot.timeframes["entry"].bar(0))

    def diagnostic_dimensions(self) -> list:
        return []

    # ---- subclass contract ----

    @abc.abstractmethod
    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        """Return "long", "short", or None. Only ever called while flat
        (per the Strategy contract) -- reason about whether the event
        fired, not about position management, which this base class owns.
        """

    def on_precompute(self, full_history: dict) -> None:
        """Override to compute subclass-specific series (VWAP, EMA slopes,
        swing pivots, ...) once precompute_batch has set
        self.bars_entry/self.entry_atr.
        """
        return

    def on_event_bar(self, snapshot: MarketSnapshot) -> None:
        """Override for state that must update every bar regardless of
        session-window gating or an open position (e.g. day-rollover
        tracking) -- the same on_bar/check_entry split ORBStrategy uses.
        """
        return

    def build_context(self, snapshot: MarketSnapshot, atr_val: float) -> dict:
        """Override to add hypothesis-specific diagnostic fields to the
        entry's context, beyond the universal "atr" already included.
        """
        return {}
