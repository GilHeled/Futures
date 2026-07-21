"""
NaiveBenchmarkStrategy: a family of naive baselines (always-long,
always-short, random-direction) that exist purely to answer "is a real
strategy's avg R/PF actually meaningful, or is it what we'd expect by
chance on this market during this period?" (see docs/SPEC.md's
verification workflow).

Deliberately shares ORBStrategy's entry timing (a fixed daily time, one
trade per day), ATR-based stop distance, and fixed R-multiple target --
the same risk *shape* ORB itself mostly uses in practice (its ATR-fallback
branch dominates real trade counts). Holding trade frequency and risk
sizing constant and varying only *which direction* is taken isolates
whether a specific entry signal adds anything beyond a coin flip (or
always picking one side) at the same time, with the same sizing.
"""

from __future__ import annotations

import random
from datetime import time as dt_time
from typing import Optional

import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.indicators import atr
from mnq_system.strategies.benchmarks.config import BenchmarkConfig
from mnq_system.strategies.common import simple_stop_target_exit
from mnq_system.strategy_api import EntrySignal, ExitDecision, MarketSnapshot, Position, Strategy, TimeframeSpec

LONG, SHORT = "long", "short"


class NaiveBenchmarkStrategy(Strategy):
    def __init__(self, cfg: BenchmarkConfig, account: AccountConfig):
        self.cfg = cfg
        self.account = account
        self.timezone = account.session.timezone

        self._current_day = None
        self._traded_today = False

        # Populated by precompute_batch()
        self.bars_entry: Optional[pd.DataFrame] = None
        self.entry_atr: Optional[pd.Series] = None

    # ---- Strategy interface ----

    @property
    def name(self) -> str:
        return f"benchmark_{self.cfg.direction}"

    @property
    def timeframes(self) -> dict:
        return {"entry": TimeframeSpec(self.cfg.entry_timeframe, warmup_bars=self.cfg.atr_period)}

    @property
    def driving_timeframe(self) -> str:
        return "entry"

    def precompute_batch(self, full_history: dict) -> None:
        self.bars_entry = full_history["entry"]
        self.entry_atr = atr(self.bars_entry, period=self.cfg.atr_period)

    def on_bar(self, snapshot: MarketSnapshot) -> None:
        et = snapshot.timeframes["entry"].now.tz_convert(self.timezone)
        day = et.date()
        if day != self._current_day:
            self._current_day = day
            self._traded_today = False

    def check_entry(self, snapshot: MarketSnapshot) -> Optional[EntrySignal]:
        entry_view = snapshot.timeframes["entry"]
        j = entry_view.pos
        bar = entry_view.bar(0)
        et = entry_view.now.tz_convert(self.timezone)

        if self._traded_today:
            return None
        if et.time() < dt_time(*self.cfg.entry_time):
            return None

        atr_val = self.entry_atr.iloc[j]
        if pd.isna(atr_val) or atr_val <= 0:
            return None

        direction = self._pick_direction(et.date())
        self._traded_today = True  # at most one attempt per day, whether or not it's ultimately opened

        entry_price = bar.close
        sign = 1 if direction == LONG else -1
        stop = entry_price - self.cfg.stop_atr_mult * atr_val * sign
        risk = abs(entry_price - stop)
        target = entry_price + risk * self.cfg.target_r_multiple * sign

        return EntrySignal(
            direction=direction, setup_type=self.name, entry_price=entry_price, stop_price=stop,
            targets=[target], context={"atr": float(atr_val)},
        )

    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        return simple_stop_target_exit(position, snapshot.timeframes["entry"].bar(0))

    def diagnostic_dimensions(self) -> list:
        return []

    # ---- private helpers ----

    def _pick_direction(self, day) -> str:
        if self.cfg.direction in (LONG, SHORT):
            return self.cfg.direction
        # Deterministic per-day draw (seeded by the calendar day + configured
        # seed) so reruns are reproducible -- a "random" baseline whose
        # results changed every run would undermine comparing it against a
        # cached backtest's results.
        rng = random.Random(day.toordinal() * 1_000_003 + self.cfg.random_seed)
        return rng.choice([LONG, SHORT])
