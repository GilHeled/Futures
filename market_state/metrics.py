"""
Evaluation metrics for Target A (§7, §9, §10). Pure numpy — deterministic and
dependency-light.

Primary: QLIKE (robust to noise in the realized-variance proxy).
Secondary: log-RV MSE/MAE, out-of-sample R², incremental R² vs. baseline, and
the Mincer–Zarnowitz unbiasedness regression.
Diagnostic (LMP, §10): rank-based AUC and decile reliability.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _clean(*arrays):
    arrs = [np.asarray(a, dtype=float) for a in arrays]
    mask = np.ones(len(arrs[0]), dtype=bool)
    for a in arrs:
        mask &= np.isfinite(a)
    return [a[mask] for a in arrs]


def qlike(realized_var, forecast_var) -> float:
    """Mean QLIKE loss = mean( rv/h - ln(rv/h) - 1 ), lower is better, 0 = perfect.
    Both inputs must be strictly positive variances."""
    rv, h = _clean(realized_var, forecast_var)
    ok = (rv > 0) & (h > 0)
    rv, h = rv[ok], h[ok]
    r = rv / h
    return float(np.mean(r - np.log(r) - 1.0))


def qlike_contributions(realized_var, forecast_var):
    """Per-observation QLIKE contributions (mean of these == qlike(...)). NaN where
    inputs are non-positive/non-finite, so callers can align them to dates."""
    rv = np.asarray(realized_var, dtype=float)
    h = np.asarray(forecast_var, dtype=float)
    ok = np.isfinite(rv) & np.isfinite(h) & (rv > 0) & (h > 0)
    out = np.full(len(rv), np.nan)
    r = rv[ok] / h[ok]
    out[ok] = r - np.log(r) - 1.0
    return out


def qlike_reduction(baseline_qlike: float, model_qlike: float) -> float:
    """Relative QLIKE reduction of the model vs. baseline (positive = better)."""
    if baseline_qlike == 0:
        return float("nan")
    return (baseline_qlike - model_qlike) / baseline_qlike


def log_rv_mse(y_true, y_pred) -> float:
    yt, yp = _clean(y_true, y_pred)
    return float(np.mean((yt - yp) ** 2))


def log_rv_mae(y_true, y_pred) -> float:
    yt, yp = _clean(y_true, y_pred)
    return float(np.mean(np.abs(yt - yp)))


def r2_vs_mean(y_true, y_pred) -> float:
    """Standard out-of-sample R² of log-RV about the realized mean."""
    yt, yp = _clean(y_true, y_pred)
    sse = np.sum((yt - yp) ** 2)
    sst = np.sum((yt - np.mean(yt)) ** 2)
    return float(1.0 - sse / sst) if sst > 0 else float("nan")


def incremental_r2(y_true, y_pred_model, y_pred_baseline) -> float:
    """Skill of the model over the baseline in log-RV MSE: 1 - SSE_model/SSE_base.
    Positive => the model reduces log-RV squared error relative to the baseline."""
    yt, ym, yb = _clean(y_true, y_pred_model, y_pred_baseline)
    sse_m = np.sum((yt - ym) ** 2)
    sse_b = np.sum((yt - yb) ** 2)
    return float(1.0 - sse_m / sse_b) if sse_b > 0 else float("nan")


def mincer_zarnowitz(realized, forecast) -> dict:
    """OLS realized ~ intercept + slope * forecast (unbiasedness: slope≈1, intercept≈0).
    `realized` and `forecast` should be in the SAME units (log-RV per §9)."""
    y, x = _clean(realized, forecast)
    X = np.column_stack([np.ones_like(x), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    intercept, slope = float(beta[0]), float(beta[1])
    resid = y - X @ beta
    sse = np.sum(resid ** 2)
    sst = np.sum((y - np.mean(y)) ** 2)
    r2 = float(1.0 - sse / sst) if sst > 0 else float("nan")
    return {"intercept": intercept, "slope": slope, "r2": r2}


def auc(score, event) -> float:
    """Rank-based AUC (Mann–Whitney), tie-safe. `event` is binary {0,1}."""
    s, e = _clean(score, event)
    pos = s[e == 1]
    neg = s[e == 0]
    n_pos, n_neg = len(pos), len(neg)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=float)
    ranks[order] = np.arange(1, len(s) + 1)
    # average ranks for ties
    _assign_tie_ranks(s, ranks)
    sum_ranks_pos = ranks[e == 1].sum()
    u = sum_ranks_pos - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def _assign_tie_ranks(values, ranks):
    order = np.argsort(values, kind="mergesort")
    sv = values[order]
    i = 0
    n = len(sv)
    while i < n:
        j = i
        while j + 1 < n and sv[j + 1] == sv[i]:
            j += 1
        if j > i:
            avg = np.mean(ranks[order[i:j + 1]])
            ranks[order[i:j + 1]] = avg
        i = j + 1


def decile_reliability(score, outcome, n_bins: int = 10) -> pd.DataFrame:
    """Bin by score quantiles; report mean score and mean outcome per bin.
    Used for LMP reliability and for RV-forecast calibration curves."""
    s, o = _clean(score, outcome)
    if len(s) == 0:
        return pd.DataFrame(columns=["bin", "n", "mean_score", "mean_outcome"])
    ranks = pd.Series(s).rank(method="first")
    bins = pd.qcut(ranks, q=min(n_bins, len(np.unique(ranks))), labels=False, duplicates="drop")
    df = pd.DataFrame({"bin": bins, "score": s, "outcome": o})
    g = df.groupby("bin")
    return pd.DataFrame({
        "bin": g.size().index.astype(int),
        "n": g.size().values,
        "mean_score": g["score"].mean().values,
        "mean_outcome": g["outcome"].mean().values,
    }).reset_index(drop=True)
