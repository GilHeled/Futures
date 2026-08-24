"""Significance, robustness, and benchmark statistics — frozen §6, §8.

All operate on a daily net-return-like series (risk units); Sharpe and the
bootstrap sign test are invariant to the arbitrary risk-unit scale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from trend_carry import config as C


def ann_sharpe(daily: pd.Series) -> float:
    d = daily.dropna().values
    if d.size < 2 or d.std(ddof=1) == 0:
        return 0.0
    return float(d.mean() / d.std(ddof=1) * np.sqrt(C.TRADING_DAYS))


def max_drawdown(daily: pd.Series) -> float:
    """Max drawdown of the cumulative sum, as a fraction of peak-scaled equity.

    Reported on a series rescaled to VOL_TARGET so the number is interpretable.
    """
    d = daily.dropna()
    if d.empty or d.std() == 0:
        return 0.0
    scale = (C.VOL_TARGET_ANNUAL / np.sqrt(C.TRADING_DAYS)) / d.std()
    eq = (1.0 + d * scale).cumprod()
    return float((eq / eq.cummax() - 1.0).min())


def block_bootstrap_p(daily: pd.Series, block: int = C.BOOTSTRAP_BLOCK_DAYS,
                      resamples: int = C.BOOTSTRAP_RESAMPLES,
                      seed: int = C.BOOTSTRAP_SEED) -> float:
    """P(mean net <= 0) via circular block bootstrap."""
    d = daily.dropna().values
    n = d.size
    if n < block * 2:
        return 1.0
    rng = np.random.default_rng(seed)
    nblocks = int(np.ceil(n / block))
    starts = rng.integers(0, n, size=(resamples, nblocks))
    offs = np.arange(block)
    means = np.empty(resamples)
    for i in range(resamples):
        idx = (starts[i][:, None] + offs).ravel()[:n] % n
        means[i] = d[idx].mean()
    return float((means <= 0).mean())


def probabilistic_sharpe(daily: pd.Series, sr_star: float = 0.0,
                         n_trials: int = 1) -> float:
    """Deflated/probabilistic Sharpe: P(true SR > sr_star), skew/kurt adjusted.

    With n_trials>1 the benchmark SR* is raised to the expected maximum SR under
    that many independent trials (Bailey & Lopez de Prado). Phase 1 uses a single
    pre-registered spec (n_trials=1), so this reduces to the PSR against 0.
    """
    d = daily.dropna().values
    n = d.size
    if n < 3 or d.std(ddof=1) == 0:
        return 0.0
    sr = d.mean() / d.std(ddof=1)            # per-observation SR
    sk = float(sps.skew(d)); ku = float(sps.kurtosis(d, fisher=False))
    if n_trials > 1:
        # expected max of N iid standard-normal SR estimates * SR estimation sd
        e = 0.5772156649
        z = sps.norm.ppf(1 - 1.0 / n_trials) * (1 - e) + \
            sps.norm.ppf(1 - 1.0 / (n_trials * np.e)) * e
        sr_star = sr_star + z / np.sqrt(n - 1)
    denom = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    return float(sps.norm.cdf((sr - sr_star) * np.sqrt(n - 1) / denom))


# --------------------------------------------------------------------------- #
# Benchmarks                                                                  #
# --------------------------------------------------------------------------- #
def random_sign_null(returns: pd.DataFrame, front_close: pd.DataFrame,
                     roots, window, cost_mult: float, base_signal: pd.DataFrame,
                     seeds: int = C.RANDOM_NULL_SEEDS) -> np.ndarray:
    """Sharpe distribution of random-direction books with matched position
    *magnitude* (|signal|) and timing, only the sign randomized."""
    from trend_carry.backtest import run_backtest
    mag = base_signal[roots].abs()
    out = np.empty(seeds)
    for k in range(seeds):
        rng = np.random.default_rng(C.BOOTSTRAP_SEED + k)
        signs = pd.DataFrame(
            rng.choice([-1.0, 1.0], size=mag.shape), index=mag.index, columns=roots)
        res = run_backtest(returns, mag * signs, front_close, roots, cost_mult)
        out[k] = ann_sharpe(res.window(*window).net)
    return out


def passive_beta_sharpe(returns, front_close, window, cost_mult, base_signal,
                        eq_roots=("ES", "NQ")) -> tuple[float, pd.Series]:
    """Long-only equity book (matched sizing), as the 'is this just beta?' bench."""
    from trend_carry.backtest import run_backtest
    roots = list(eq_roots)
    long_sig = pd.DataFrame(1.0, index=base_signal.index, columns=roots)
    res = run_backtest(returns, long_sig, front_close, roots, cost_mult)
    net = res.window(*window).net
    return ann_sharpe(net), net


def beta_alpha(port_daily: pd.Series, spx_daily: pd.Series) -> dict:
    """OLS of the book on equity (ES) returns: alpha (per day), beta, corr, R^2."""
    df = pd.concat([port_daily.rename("p"), spx_daily.rename("m")], axis=1).dropna()
    if len(df) < 10 or df["m"].std() == 0:
        return {"alpha": 0.0, "beta": 0.0, "corr": 0.0, "r2": 0.0}
    b, a, r, _, _ = sps.linregress(df["m"], df["p"])
    return {"alpha": float(a), "beta": float(b), "corr": float(r), "r2": float(r ** 2)}


# --------------------------------------------------------------------------- #
# Breadth                                                                     #
# --------------------------------------------------------------------------- #
def sector_sharpes(net_inst: pd.DataFrame) -> dict[str, float]:
    out = {}
    for sec in C.SECTORS:
        cols = [c for c in net_inst.columns if C.BY_ROOT[c].sector == sec]
        out[sec] = ann_sharpe(net_inst[cols].sum(axis=1))
    return out


def drop_best_sector_sharpe(net_inst: pd.DataFrame) -> tuple[float, str]:
    """Sharpe of the whole book with the single best sector removed."""
    secs = sector_sharpes(net_inst)
    best = max(secs, key=secs.get)
    keep = [c for c in net_inst.columns if C.BY_ROOT[c].sector != best]
    return ann_sharpe(net_inst[keep].sum(axis=1)), best


def yearly_sharpes(net: pd.Series) -> dict[int, float]:
    return {int(y): ann_sharpe(g) for y, g in net.groupby(net.index.year)}


def drop_best_year_sharpe(net: pd.Series) -> tuple[float, int]:
    ys = yearly_sharpes(net)
    best = max(ys, key=ys.get)
    return ann_sharpe(net[net.index.year != best]), best
