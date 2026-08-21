"""OHLCV bar model for ict_live. Times are timezone-aware (ET). A bar is immutable
once closed; a still-aggregating higher-timeframe bar carries `forming=True` and must
never be used to confirm a signal (causality)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    timeframe: str          # "1m","5m","15m","1H","4H","D","W"
    open_time: datetime     # tz-aware (ET), inclusive start
    close_time: datetime    # tz-aware (ET), exclusive end (== next bar's open_time)
    open: float
    high: float
    low: float
    close: float
    volume: float
    forming: bool = False   # True == higher-TF bar still aggregating (context-only)

    def __post_init__(self):
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) \
                or self.high < self.low:
            raise ValueError(f"invalid OHLC: {self}")
        if self.close_time <= self.open_time:
            raise ValueError(f"close_time must be after open_time: {self}")
