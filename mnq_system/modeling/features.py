"""
Market-state feature matrix: one row per entry-timeframe bar, entirely
causal -- every feature at position `j` depends only on bars `<= j`.
Reuses existing indicator/regime/swing/session primitives rather than
reimplementing them; see mnq_system/modeling/labels.py for the (forward-
looking, causality-exempt-by-design) supervised targets these features are
paired with.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dt_time

import numpy as np
import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.indicators import atr, session_vwap
from mnq_system.regime import ema_slope_pct, rolling_percentile
from mnq_system.session_features import overnight_signed_volume_imbalance_by_date, prior_session_close_by_date
from mnq_system.swings import compute_swings


@dataclass(frozen=True)
class FeatureConfig:
    atr_period: int = 14
    volatility_lookback_bars: int = 2000  # for atr_regime_pctile's rolling percentile
    trend_ema_period: int = 50
    trend_slope_lookback: int = 10
    swing_lookback: int = 2
    session_close_time: tuple = (16, 0)  # ET -- prior-session-close / overnight-window reference
    session_open_time: tuple = (9, 30)  # ET -- NY session open / overnight-window end
    overnight_imbalance_lookback_days: int = 60


DEFAULT_FEATURE_CONFIG = FeatureConfig()

FEATURE_COLUMNS = [
    "atr_pct_of_price", "atr_regime_pctile", "vwap_distance_atr", "trend_slope_pct",
    "dist_to_swing_high_atr", "dist_to_swing_low_atr", "bars_since_sweep",
    "gap_atr_ratio", "overnight_imbalance_pctile", "hour_et", "weekday",
]


def _confirmed_swing_price(bars: pd.DataFrame, swings: pd.DataFrame, lookback: int, kind: str) -> pd.Series:
    """Vectorized equivalent of mnq_system.swings.latest_confirmed_swing
    evaluated at every position: a swing high/low confirmed at position p
    (i.e. known `lookback` bars after it occurred) only becomes visible
    starting at position p + lookback, then holds until superseded.
    """
    price_col = "high" if kind == "high" else "low"
    is_swing_col = "is_swing_high" if kind == "high" else "is_swing_low"
    revealed_at_confirmation = bars[price_col].where(swings[is_swing_col]).shift(lookback)
    return revealed_at_confirmation.ffill()


def build_feature_matrix(
    bars_by_timeframe: dict, account: AccountConfig, cfg: FeatureConfig = DEFAULT_FEATURE_CONFIG
) -> pd.DataFrame:
    bars = bars_by_timeframe["entry"]
    timezone = account.session.timezone
    close = bars["close"]
    n = len(bars)

    atr_series = atr(bars, period=cfg.atr_period)
    atr_safe = atr_series.where(atr_series > 0)

    vwap = session_vwap(bars, tz=timezone)
    trend_slope_pct = ema_slope_pct(close, cfg.trend_ema_period, cfg.trend_slope_lookback)

    swings = compute_swings(bars, lookback=cfg.swing_lookback)
    confirmed_high = _confirmed_swing_price(bars, swings, cfg.swing_lookback, "high")
    confirmed_low = _confirmed_swing_price(bars, swings, cfg.swing_lookback, "low")

    swept_high = (bars["high"] > confirmed_high) & (close < confirmed_high)
    swept_low = (bars["low"] < confirmed_low) & (close > confirmed_low)
    any_sweep = (swept_high | swept_low).to_numpy()
    positions = np.arange(n)
    last_sweep_pos = pd.Series(np.where(any_sweep, positions, np.nan), index=bars.index).ffill()
    bars_since_sweep = positions - last_sweep_pos.fillna(-1).to_numpy()

    et_index = bars.index.tz_convert(timezone)
    times = et_index.time
    dates = et_index.date

    prior_close_by_date = prior_session_close_by_date(bars, timezone, cfg.session_close_time)
    open_time = dt_time(*cfg.session_open_time)
    day_open_by_date = (
        pd.DataFrame({"date": dates, "time": times, "open": bars["open"].to_numpy()})
        .loc[lambda d: d["time"] >= open_time]
        .groupby("date")["open"]
        .first()
        .to_dict()
    )
    gap_raw_by_date = {
        d: day_open_by_date[d] - prior_close_by_date[d]
        for d in day_open_by_date
        if d in prior_close_by_date
    }
    gap_raw = np.array([gap_raw_by_date.get(d, np.nan) for d in dates])

    imbalance_by_date = overnight_signed_volume_imbalance_by_date(
        bars, timezone, cfg.session_close_time, cfg.session_open_time
    )
    imbalance_series = pd.Series(imbalance_by_date).sort_index()
    imbalance_pctile_by_date = rolling_percentile(
        imbalance_series.abs(), lookback=cfg.overnight_imbalance_lookback_days
    ).to_dict()
    overnight_imbalance_pctile = np.array([imbalance_pctile_by_date.get(d, np.nan) for d in dates])

    features = pd.DataFrame(
        {
            "atr_pct_of_price": (atr_series / close).to_numpy(),
            "atr_regime_pctile": rolling_percentile(atr_series, lookback=cfg.volatility_lookback_bars).to_numpy(),
            "vwap_distance_atr": ((close - vwap) / atr_safe).to_numpy(),
            "trend_slope_pct": trend_slope_pct.to_numpy(),
            "dist_to_swing_high_atr": ((confirmed_high - close) / atr_safe).to_numpy(),
            "dist_to_swing_low_atr": ((close - confirmed_low) / atr_safe).to_numpy(),
            "bars_since_sweep": bars_since_sweep,
            "gap_atr_ratio": gap_raw / atr_safe.to_numpy(),
            "overnight_imbalance_pctile": overnight_imbalance_pctile,
            "hour_et": et_index.hour.to_numpy(),
            "weekday": et_index.dayofweek.to_numpy(),
        },
        index=bars.index,
    )
    return features[FEATURE_COLUMNS]
