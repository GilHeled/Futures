"""
ORBStrategy: plain Opening Range Breakout on a single timeframe. Tracks the
high/low of a fixed morning window every day (`on_bar`, which the engine
calls unconditionally regardless of session-window gating or open
positions -- unlike `check_entry`, which the engine only calls while flat
and inside an account trading window). A breakout of that range, before a
cutoff time and at most once per day, opens a position with either the
opposite side of the range or an ATR-based stop (whichever the range's own
width calls for) and a fixed R-multiple target.

Deliberately structurally different from EmaFibReversalStrategy: no bias
filter, no Fibonacci zones, no candlestick confirmation, no reversal state
machine -- a clean baseline to test whether a plain breakout idea has any
robust signal on MNQ, independent of whether the EMA/Fib rule set did.
"""

from __future__ import annotations

from datetime import time as dt_time
from typing import Optional

import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.indicators import atr
from mnq_system.regime import VOLATILITY_BUCKETS, bucket_percentile, ema_slope_pct, rolling_percentile
from mnq_system.strategies.common import simple_stop_target_exit
from mnq_system.strategies.orb.config import ORBConfig
from mnq_system.strategy_api import EntrySignal, ExitDecision, MarketSnapshot, Position, Strategy, TimeframeSpec

LONG, SHORT = "long", "short"
VOLATILITY_LOOKBACK_BARS = 2000  # ~1 week of continuous 5m bars; reporting-only, not a trading rule
TREND_EMA_PERIOD = 20
TREND_SLOPE_LOOKBACK_BARS = 10


class ORBStrategy(Strategy):
    def __init__(self, cfg: ORBConfig, account: AccountConfig):
        self.cfg = cfg
        self.account = account
        self.timezone = account.session.timezone

        self._current_day = None
        self._or_high: Optional[float] = None
        self._or_low: Optional[float] = None
        self._traded_today = False

        # Populated by precompute_batch()
        self.bars_entry: Optional[pd.DataFrame] = None
        self.entry_atr: Optional[pd.Series] = None
        self.entry_atr_percentile: Optional[pd.Series] = None
        self.entry_trend_percentile: Optional[pd.Series] = None

    # ---- Strategy interface ----

    @property
    def name(self) -> str:
        return "orb"

    @property
    def timeframes(self) -> dict:
        return {"entry": TimeframeSpec(self.cfg.entry_timeframe, warmup_bars=self.cfg.atr_period)}

    @property
    def driving_timeframe(self) -> str:
        return "entry"

    def precompute_batch(self, full_history: dict) -> None:
        self.bars_entry = full_history["entry"]
        self.entry_atr = atr(self.bars_entry, period=self.cfg.atr_period)
        self.entry_atr_percentile = rolling_percentile(self.entry_atr, lookback=VOLATILITY_LOOKBACK_BARS)

        slope_pct = ema_slope_pct(self.bars_entry["close"], TREND_EMA_PERIOD, TREND_SLOPE_LOOKBACK_BARS).abs()
        self.entry_trend_percentile = rolling_percentile(slope_pct, lookback=VOLATILITY_LOOKBACK_BARS)

    def on_bar(self, snapshot: MarketSnapshot) -> None:
        # Opening-range accumulation must happen every bar regardless of
        # session-window gating or an open position (unlike check_entry,
        # which the engine only calls while flat and inside a trading
        # window) -- so this tracking lives here, not in check_entry.
        entry_view = snapshot.timeframes["entry"]
        bar = entry_view.bar(0)
        et = entry_view.now.tz_convert(self.timezone)
        day = et.date()

        if day != self._current_day:
            self._current_day = day
            self._or_high = None
            self._or_low = None
            self._traded_today = False

        if dt_time(*self.cfg.or_start) <= et.time() < dt_time(*self.cfg.or_end):
            self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
            self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)

    def check_entry(self, snapshot: MarketSnapshot) -> Optional[EntrySignal]:
        entry_view = snapshot.timeframes["entry"]
        j = entry_view.pos
        bar = entry_view.bar(0)
        et_time = entry_view.now.tz_convert(self.timezone).time()

        if et_time < dt_time(*self.cfg.or_end):
            return None  # opening range hasn't closed yet
        if self._traded_today or self._or_high is None or self._or_low is None:
            return None
        if et_time >= dt_time(*self.cfg.entry_cutoff):
            return None

        atr_val = self.entry_atr.iloc[j]
        if pd.isna(atr_val) or atr_val <= 0:
            return None  # ATR not warmed up yet

        range_size = self._or_high - self._or_low
        if range_size <= 0:
            return None

        if bar.high >= self._or_high:
            direction, entry_price = LONG, self._or_high
        elif bar.low <= self._or_low:
            direction, entry_price = SHORT, self._or_low
        else:
            return None

        sign = 1 if direction == LONG else -1
        wide_range = range_size > self.cfg.max_range_atr_mult * atr_val
        if wide_range:
            stop = entry_price - self.cfg.stop_atr_mult * atr_val * sign
        else:
            stop = self._or_low if direction == LONG else self._or_high

        risk = abs(entry_price - stop)
        if risk <= 0:
            return None
        target = entry_price + risk * self.cfg.target_r_multiple * sign

        # A day gets at most one breakout attempt, whether or not it's
        # ultimately opened (e.g. sizing rounds to 0 contracts) -- "one
        # trade per day maximum" is this strategy's own rule, not just an
        # account-level limit.
        self._traded_today = True

        context = self._build_context(j, atr_val, range_size, wide_range)
        return EntrySignal(
            direction=direction, setup_type="orb_breakout", entry_price=entry_price, stop_price=stop,
            targets=[target], context=context,
        )

    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        return simple_stop_target_exit(position, snapshot.timeframes["entry"].bar(0))

    def diagnostic_dimensions(self) -> list:
        return ["range_size_bucket", "atr_regime", "trend_regime", "stop_type"]

    # ---- private helpers ----

    def _build_context(self, j: int, atr_val: float, range_size: float, wide_range: bool) -> dict:
        range_atr_ratio = range_size / atr_val
        if range_atr_ratio < 1.0:
            range_size_bucket = "narrow"
        elif range_atr_ratio < self.cfg.max_range_atr_mult:
            range_size_bucket = "moderate"
        else:
            range_size_bucket = "wide"

        atr_pct_rank = self.entry_atr_percentile.iloc[j]
        trend_pct_rank = self.entry_trend_percentile.iloc[j]

        return {
            "or_high": self._or_high,
            "or_low": self._or_low,
            "range_size": float(range_size),
            "range_size_atr_ratio": float(range_atr_ratio),
            "range_size_bucket": range_size_bucket,
            "atr": float(atr_val),
            "atr_regime": bucket_percentile(atr_pct_rank, buckets=VOLATILITY_BUCKETS),
            "trend_regime": bucket_percentile(trend_pct_rank, buckets=("choppy", "neutral", "trending")),
            "stop_type": "atr_fallback" if wide_range else "range_opposite",
        }
