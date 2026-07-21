"""
Hypothesis: does an overnight order-flow imbalance -- estimated from
volume-signed price direction across the overnight (Globex) session --
predict the first hour of the NY session?

Deliberately NOT the same quantity as opening_gap's raw price gap (which
would make the two hypotheses numerically identical, since both would
otherwise collapse to `open_0930 - close_1600`). This uses each overnight
bar's own volume, signed by whether that bar closed up or down, as a crude
order-flow proxy -- the closest approximation to "inventory imbalance"
available from OHLCV-only data (no true order-flow/tick data).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import time as dt_time
from typing import Optional

import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.regime import rolling_percentile
from mnq_system.session_features import overnight_signed_volume_imbalance_by_date
from mnq_system.strategies.hypotheses.base import (
    HypothesisExitConfig,
    HypothesisStrategy,
    exit_cfg_from_args,
)
from mnq_system.strategy_api import MarketSnapshot

__all__ = [
    "OvernightImbalanceConfig", "OvernightImbalanceStrategy", "DEFAULT_CONFIG",
    "add_cli_arguments", "build_from_cli_args",
]


@dataclass(frozen=True)
class OvernightImbalanceConfig:
    entry_timeframe: str = "5m"
    exit: HypothesisExitConfig = field(default_factory=HypothesisExitConfig)
    session_close_time: tuple = (16, 0)  # ET -- overnight window start
    session_open_time: tuple = (9, 30)  # ET -- overnight window end / entry bar
    imbalance_lookback_days: int = 60  # trailing days used to rank this day's |imbalance|
    min_imbalance_percentile: float = 0.75  # only bet when |imbalance| is in the top quartile of its trailing history


DEFAULT_CONFIG = OvernightImbalanceConfig()


class OvernightImbalanceStrategy(HypothesisStrategy):
    def __init__(self, cfg: OvernightImbalanceConfig, account: AccountConfig):
        self.cfg = cfg
        super().__init__(cfg.exit, account, entry_timeframe=cfg.entry_timeframe, warmup_bars=0)
        self._imbalance_by_date: dict = {}
        self._percentile_by_date: dict = {}
        self._fired_today = False
        self._current_day = None

    @property
    def name(self) -> str:
        return "overnight_imbalance"

    def on_precompute(self, full_history: dict) -> None:
        self._imbalance_by_date = overnight_signed_volume_imbalance_by_date(
            self.bars_entry, self.timezone, self.cfg.session_close_time, self.cfg.session_open_time
        )
        daily_imbalance = pd.Series(self._imbalance_by_date).sort_index()
        pct_series = rolling_percentile(daily_imbalance.abs(), lookback=self.cfg.imbalance_lookback_days)
        self._percentile_by_date = pct_series.to_dict()

    def on_event_bar(self, snapshot: MarketSnapshot) -> None:
        et = snapshot.timeframes["entry"].now.tz_convert(self.timezone)
        day = et.date()
        if day != self._current_day:
            self._current_day = day
            self._fired_today = False

    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        et = snapshot.timeframes["entry"].now.tz_convert(self.timezone)

        if self._fired_today:
            return None
        if et.time() < dt_time(*self.cfg.session_open_time):
            return None

        self._fired_today = True  # at most one bet per day

        day = et.date()
        imbalance = self._imbalance_by_date.get(day)
        pct = self._percentile_by_date.get(day)
        if imbalance is None or pct is None or pd.isna(pct) or pct < self.cfg.min_imbalance_percentile:
            return None
        return "long" if imbalance > 0 else "short"

    def build_context(self, snapshot: MarketSnapshot, atr_val: float) -> dict:
        day = snapshot.timeframes["entry"].now.tz_convert(self.timezone).date()
        return {
            "overnight_imbalance": float(self._imbalance_by_date.get(day, float("nan"))),
            "overnight_imbalance_percentile": float(self._percentile_by_date.get(day, float("nan"))),
        }


def add_cli_arguments(parser) -> None:
    parser.add_argument("--overnight-lookback-days", type=int, default=DEFAULT_CONFIG.imbalance_lookback_days)
    parser.add_argument("--overnight-min-percentile", type=float, default=DEFAULT_CONFIG.min_imbalance_percentile)


def build_from_cli_args(args) -> OvernightImbalanceConfig:
    return replace(
        DEFAULT_CONFIG, exit=exit_cfg_from_args(args), imbalance_lookback_days=args.overnight_lookback_days,
        min_imbalance_percentile=args.overnight_min_percentile,
    )
