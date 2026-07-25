"""
Day-level block bootstrap (§8, §9).

Inference respects intraday autocorrelation by resampling contiguous multi-day
BLOCKS rather than individual observations. The unit is a per-day statistic
(e.g. the daily mean paired QLIKE improvement); we resample circular 5-day
blocks, recompute the mean, and read off P(mean <= 0) and a percentile CI.
"""
from __future__ import annotations

import numpy as np

from market_state import config as C


def block_bootstrap_mean(daily_values, block_days: int = C.BOOTSTRAP_BLOCK_DAYS,
                         resamples: int = C.BOOTSTRAP_RESAMPLES,
                         seed: int = C.BOOTSTRAP_SEED,
                         ci: float = 0.95) -> dict:
    """Circular block bootstrap of the mean of a per-day array.
    Returns the observed mean, P(mean <= 0), and a percentile CI."""
    x = np.asarray(daily_values, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n == 0:
        return {"mean": float("nan"), "p_mean_le_zero": float("nan"),
                "ci_lo": float("nan"), "ci_hi": float("nan"), "n_days": 0}
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_days))
    starts = rng.integers(0, n, size=(resamples, n_blocks))
    offsets = np.arange(block_days)
    means = np.empty(resamples, dtype=float)
    for r in range(resamples):
        idx = (starts[r][:, None] + offsets[None, :]).ravel() % n
        means[r] = x[idx[:n]].mean()
    alpha = (1.0 - ci) / 2.0
    return {
        "mean": float(x.mean()),
        "p_mean_le_zero": float(np.mean(means <= 0.0)),
        "ci_lo": float(np.quantile(means, alpha)),
        "ci_hi": float(np.quantile(means, 1.0 - alpha)),
        "n_days": int(n),
    }


def daily_paired_improvement(dates, baseline_loss, model_loss) -> tuple:
    """Aggregate per-observation losses (e.g. per-bar QLIKE contributions) into a
    per-day paired improvement = mean(baseline_loss) - mean(model_loss) per day.
    Returns (unique_days, improvement_per_day)."""
    import pandas as pd
    df = pd.DataFrame({"date": np.asarray(dates),
                       "base": np.asarray(baseline_loss, dtype=float),
                       "model": np.asarray(model_loss, dtype=float)})
    df = df[np.isfinite(df["base"]) & np.isfinite(df["model"])]
    g = df.groupby("date")
    imp = g["base"].mean() - g["model"].mean()
    return imp.index.to_numpy(), imp.to_numpy()
