"""
The single LOCKED prediction model (docs/IMPLEMENTATION.md §1 + v2 §7.1): Ridge
regression of log-RV on standardized features, with α chosen by nested, purged,
in-fold time-series CV on TRAIN data only, and a v2 Duan smearing retransformation
`h = s_f · exp(μ̂)` whose factor `s_f` is estimated from leakage-safe out-of-fold
TRAIN residuals in a dedicated pass at α* (selection and calibration kept separate).
Deterministic; the only data-fit scalars beyond the coefficients are α and s_f.
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from market_state import config as C
from market_state import metrics as M
from market_state.purged_cv import purged_walk_forward_splits

ALPHA_GRID = (0.01, 0.1, 1.0, 10.0, 100.0)
INNER_K = C.INNER_K


def _fit_scaler_ridge(X, y, alpha):
    scaler = StandardScaler().fit(X)
    ridge = Ridge(alpha=alpha).fit(scaler.transform(X), y)
    return scaler, ridge


def _inner_oof(X, y, rv, entry_pos, exit_pos, alpha, inner_k, embargo):
    """One leakage-safe out-of-fold pass over the training rows at a fixed α.
    Returns pooled OOF (μ̂, y, rv) across the inner purged walk-forward."""
    inner = list(purged_walk_forward_splits(entry_pos, exit_pos,
                                            n_splits=inner_k + 1, embargo_bars=embargo))
    mus, ys, rvs = [], [], []
    for tr, va in inner:
        scaler, ridge = _fit_scaler_ridge(X[tr], y[tr], alpha)
        mus.append(ridge.predict(scaler.transform(X[va])))
        ys.append(y[va])
        rvs.append(rv[va])
    if not mus:
        return np.array([]), np.array([]), np.array([])
    return np.concatenate(mus), np.concatenate(ys), np.concatenate(rvs)


def select_alpha(X, y, rv, entry_pos, exit_pos,
                 grid=ALPHA_GRID, inner_k=INNER_K, embargo=C.EMBARGO_BARS):
    """Choose α minimizing inner OOF-**smeared** QLIKE (SMEARING_SCOPE='all_qlike'):
    each α is scored on its own OOF smearing factor. TRAIN-only; no outer-test leakage."""
    scores = {}
    for alpha in grid:
        mu, yv, rvv = _inner_oof(X, y, rv, entry_pos, exit_pos, alpha, inner_k, embargo)
        if len(mu) == 0:
            scores[alpha] = float("inf")
            continue
        s_alpha = float(np.mean(np.exp(yv - mu)))          # per-α smearing (scoring only)
        scores[alpha] = M.qlike(rvv, s_alpha * np.exp(mu))
    best = min(grid, key=lambda a: scores[a])
    return best, scores


def smearing_factor(X, y, rv, entry_pos, exit_pos, alpha,
                    inner_k=INNER_K, embargo=C.EMBARGO_BARS):
    """Duan smearing factor for a fold: a DEDICATED out-of-fold pass at the given α
    (kept separate from α-selection). s = mean(exp(OOF residuals))."""
    mu, yv, _ = _inner_oof(X, y, rv, entry_pos, exit_pos, alpha, inner_k, embargo)
    if len(mu) == 0:
        return 1.0
    return float(np.mean(np.exp(yv - mu)))


def fit_predict(X_train, y_train, rv_train, entry_train, exit_train, X_test,
                grid=ALPHA_GRID, inner_k=INNER_K, embargo=C.EMBARGO_BARS):
    """One outer fold, v2: select α (nested OOF-smeared QLIKE), estimate s_f in a
    dedicated OOF pass at α*, refit on full train, predict test, retransform.
    Returns (μ̂_test, h_test, α*, s_f, alpha_scores). The LMP score is μ̂_test."""
    best_alpha, scores = select_alpha(
        X_train, y_train, rv_train, entry_train, exit_train, grid, inner_k, embargo)
    s_f = smearing_factor(
        X_train, y_train, rv_train, entry_train, exit_train, best_alpha, inner_k, embargo)
    scaler, ridge = _fit_scaler_ridge(X_train, y_train, best_alpha)
    mu_test = ridge.predict(scaler.transform(X_test))
    h_test = s_f * np.exp(mu_test)
    return mu_test, h_test, best_alpha, s_f, scores
