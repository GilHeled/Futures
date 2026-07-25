"""
Trading metrics + inference (§8–§9, §11). Daily net PnL ($) in, statistics out.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from trading_value import config as C

_EULER = 0.5772156649


def ann_sharpe(daily: np.ndarray) -> float:
    d = np.asarray(daily, dtype=float)
    d = d[np.isfinite(d)]
    sd = d.std(ddof=1)
    if sd == 0 or len(d) < 2:
        return 0.0
    return float(d.mean() / sd * np.sqrt(C.TRADING_DAYS_PER_YEAR))


def daily_sharpe(daily: np.ndarray) -> float:
    d = np.asarray(daily, dtype=float)
    sd = d.std(ddof=1)
    return 0.0 if (sd == 0 or len(d) < 2) else float(d.mean() / sd)


def max_drawdown(daily: np.ndarray) -> float:
    """Peak-to-trough drawdown of the cumulative PnL curve, in $ (positive magnitude)."""
    eq = np.cumsum(np.asarray(daily, dtype=float))
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return float(dd.max()) if len(dd) else 0.0


def total_pnl(daily) -> float:
    return float(np.sum(daily))


def profit_factor(trade_pnl) -> float:
    t = np.asarray(trade_pnl, dtype=float)
    gains = t[t > 0].sum()
    losses = -t[t < 0].sum()
    return float(gains / losses) if losses > 0 else float("inf")


def paired_block_bootstrap_dsharpe(daily_fc, daily_naive,
                                   block_days=C.BOOTSTRAP_BLOCK_DAYS,
                                   resamples=C.BOOTSTRAP_RESAMPLES,
                                   seed=C.BOOTSTRAP_SEED) -> dict:
    """Circular block bootstrap of ΔSharpe (forecast − naive), resampling days
    JOINTLY (paired) so the comparison is apples-to-apples. Returns observed
    ΔSharpe, P(ΔSharpe ≤ 0), and a 95% CI."""
    a = np.asarray(daily_fc, dtype=float)
    b = np.asarray(daily_naive, dtype=float)
    n = len(a)
    obs = ann_sharpe(a) - ann_sharpe(b)
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_days))
    offs = np.arange(block_days)
    deltas = np.empty(resamples)
    for r in range(resamples):
        starts = rng.integers(0, n, size=n_blocks)
        idx = ((starts[:, None] + offs[None, :]).ravel() % n)[:n]
        deltas[r] = ann_sharpe(a[idx]) - ann_sharpe(b[idx])
    return {
        "obs_dsharpe": obs,
        "p_le_zero": float(np.mean(deltas <= 0.0)),
        "ci_lo": float(np.quantile(deltas, 0.025)),
        "ci_hi": float(np.quantile(deltas, 0.975)),
    }


def deflated_sharpe(sharpes_daily: list, T: int, skew: float, kurt: float) -> dict:
    """Deflated Sharpe Ratio (Bailey & López de Prado) for the best of N per-day
    Sharpes, guarding the forecast arm's absolute Sharpe against selection over the
    N pre-declared configs. Reported, not a hard gate."""
    s = np.asarray(sharpes_daily, dtype=float)
    N = len(s)
    sr_hat = float(s.max())
    var_across = float(s.var(ddof=1)) if N > 1 else 0.0
    sd_across = np.sqrt(var_across)
    if sd_across == 0:
        sr_star = 0.0
    else:
        sr_star = sd_across * ((1 - _EULER) * norm.ppf(1 - 1.0 / N)
                               + _EULER * norm.ppf(1 - 1.0 / (N * np.e)))
    denom = np.sqrt(max(1e-12, 1 - skew * sr_hat + (kurt - 1) / 4.0 * sr_hat ** 2))
    dsr = float(norm.cdf((sr_hat - sr_star) * np.sqrt(max(1, T - 1)) / denom))
    return {"dsr": dsr, "sr_hat_daily": sr_hat, "sr_star_daily": float(sr_star)}


def drop_best_period_mean(daily_diff: pd.Series, freq: str) -> float:
    """Mean of the daily (forecast − naive) difference after removing the single
    calendar period (freq='M' month or 'Y' year) with the largest summed difference.
    Used to check the effect is not concentrated in one isolated period."""
    s = pd.Series(np.asarray(daily_diff, dtype=float),
                  index=pd.to_datetime(pd.Index(daily_diff.index)))
    by = s.groupby(s.index.to_period(freq)).sum()
    worst_best = by.idxmax()
    keep = s[s.index.to_period(freq) != worst_best]
    return float(keep.mean()) if len(keep) else float("nan")
