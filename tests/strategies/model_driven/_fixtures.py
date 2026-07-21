"""
Shared fixture helpers for mnq_system.strategies.model_driven's per-policy
tests -- not a test file itself. Builds a small, fast multi-day synthetic
bar series (small FeatureConfig lookbacks so on_precompute's real
walk-forward retraining stays fast) with a genuine, learnable directional
pattern, for the engine end-to-end / causality tests that need
ModelDrivenStrategy's real on_precompute to run, not a hand-populated
OOS-proba fixture like test_base.py uses.
"""

from dataclasses import replace

import numpy as np
import pandas as pd

from mnq_system.config import AccountConfig, SessionConfig
from mnq_system.modeling.features import FeatureConfig
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig
from mnq_system.strategies.model_driven.base import ModelDrivenConfig

FAST_FEATURE_CFG = FeatureConfig(
    atr_period=3, volatility_lookback_bars=10, trend_ema_period=5, trend_slope_lookback=2,
    swing_lookback=1, overnight_imbalance_lookback_days=2,
)


def fast_model_driven_config(**overrides) -> ModelDrivenConfig:
    defaults = dict(
        exit=HypothesisExitConfig(atr_period=3), feature_cfg=FAST_FEATURE_CFG, n_folds=3, min_train_fraction=0.3,
        confidence_threshold=0.0,  # low bar -- these tests check wiring, not statistical power
        debounce_bars=2,  # short, so a fast/small synthetic series still produces multiple calendar entries
    )
    defaults.update(overrides)
    return ModelDrivenConfig(**defaults)


def account() -> AccountConfig:
    return replace(
        AccountConfig(),
        session=SessionConfig(trading_windows=((0, 0, 23, 59),), reduced_size_windows=(), timezone="America/New_York"),
    )


def make_multi_day_bars(n_days: int = 20, bars_per_day: int = 30, seed: int = 0) -> pd.DataFrame:
    """An oscillating trend (reverses direction every few days) with noise,
    spanning several calendar days (needed for the day-level gap/overnight-
    imbalance features to ever be non-NaN, and for forward-return labels to
    cover more than one class at every horizon -- a one-directional series
    would leave some horizons with only a single class present, which
    walk_forward_predict correctly refuses to fit a classifier on).
    5-minute bars starting each day at 09:00 ET.
    """
    rng = np.random.default_rng(seed)
    rows = []
    price = 100.0
    for day in range(n_days):
        day_start = pd.Timestamp("2026-06-01", tz="America/New_York") + pd.Timedelta(days=day)
        drift = 0.35 if (day // 3) % 2 == 0 else -0.35  # reverses every 3 days
        for b in range(bars_per_day):
            ts = day_start + pd.Timedelta(hours=9) + pd.Timedelta(minutes=5 * b)
            noise = rng.normal(scale=0.3)
            close = price + drift + noise
            high, low = max(price, close) + 0.2, min(price, close) - 0.2
            rows.append((ts, price, high, low, close, 1000))
            price = close
    idx = pd.DatetimeIndex([r[0] for r in rows])
    return pd.DataFrame(
        {
            "open": [r[1] for r in rows], "high": [r[2] for r in rows], "low": [r[3] for r in rows],
            "close": [r[4] for r in rows], "volume": [r[5] for r in rows],
        },
        index=idx,
    )
