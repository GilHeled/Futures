"""
Hypothesis: does an overnight gap -- comparing today's NY-session open to
the prior session's close -- have predictive value for the first hour's
direction (a continuation bet: bet with the gap, not against it)?
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import time as dt_time
from typing import Optional

from mnq_system.config import AccountConfig
from mnq_system.session_features import prior_session_close_by_date
from mnq_system.strategies.hypotheses.base import (
    HypothesisExitConfig,
    HypothesisStrategy,
    exit_cfg_from_args,
)
from mnq_system.strategy_api import MarketSnapshot

__all__ = ["OpeningGapConfig", "OpeningGapStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args"]


@dataclass(frozen=True)
class OpeningGapConfig:
    entry_timeframe: str = "5m"
    exit: HypothesisExitConfig = field(default_factory=HypothesisExitConfig)
    session_close_time: tuple = (16, 0)  # ET -- defines the "prior close" reference
    session_open_time: tuple = (9, 30)  # ET -- the bet fires on the first bar at/after this time
    min_gap_atr_mult: float = 0.5  # minimum gap size (in ATR) worth betting on


DEFAULT_CONFIG = OpeningGapConfig()


class OpeningGapStrategy(HypothesisStrategy):
    def __init__(self, cfg: OpeningGapConfig, account: AccountConfig):
        self.cfg = cfg
        super().__init__(cfg.exit, account, entry_timeframe=cfg.entry_timeframe, warmup_bars=0)
        self._current_day = None
        self._prior_session_close_by_date: dict = {}  # populated by precompute_batch()
        self._fired_today = False

    @property
    def name(self) -> str:
        return "opening_gap"

    def on_precompute(self, full_history: dict) -> None:
        self._prior_session_close_by_date = prior_session_close_by_date(
            self.bars_entry, self.timezone, self.cfg.session_close_time
        )

    def on_event_bar(self, snapshot: MarketSnapshot) -> None:
        et = snapshot.timeframes["entry"].now.tz_convert(self.timezone)
        day = et.date()
        if day != self._current_day:
            self._current_day = day
            self._fired_today = False

    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        view = snapshot.timeframes["entry"]
        j = view.pos
        et = view.now.tz_convert(self.timezone)
        day = et.date()

        if self._fired_today:
            return None
        if et.time() < dt_time(*self.cfg.session_open_time):
            return None

        prior_close = self._prior_session_close_by_date.get(day)
        if prior_close is None:
            return None

        self._fired_today = True  # at most one bet per day, whether or not it clears the threshold

        atr_val = self.entry_atr.iloc[j]
        gap = view.bar(0).open - prior_close
        if abs(gap) < self.cfg.min_gap_atr_mult * atr_val:
            return None
        return "long" if gap > 0 else "short"

    def build_context(self, snapshot: MarketSnapshot, atr_val: float) -> dict:
        day = snapshot.timeframes["entry"].now.tz_convert(self.timezone).date()
        prior_close = self._prior_session_close_by_date.get(day)
        gap = snapshot.timeframes["entry"].bar(0).open - prior_close
        return {"gap": float(gap), "gap_atr_ratio": float(gap / atr_val) if atr_val else None}


def add_cli_arguments(parser) -> None:
    parser.add_argument("--gap-min-atr-mult", type=float, default=DEFAULT_CONFIG.min_gap_atr_mult)


def build_from_cli_args(args) -> OpeningGapConfig:
    return replace(DEFAULT_CONFIG, exit=exit_cfg_from_args(args), min_gap_atr_mult=args.gap_min_atr_mult)
