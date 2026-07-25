"""
Candidate baselines and the frozen selection rule (§4).

All baselines forecast the FORWARD H-bar realized VARIANCE (strictly positive,
for QLIKE) and the corresponding log-RV (for R²/MZ). Forecasts are functions of
information available at the forecast bar only (causal).

  persistence   : forward variance = trailing 30-min realized variance (rv_lag6)
  ewma          : EWMA of 5-min squared returns (span) scaled by the horizon
  har           : OLS of log-RV on [log rv_lag6, log rv_lag24, log rv_prev_session]
                  (fit on TRAIN rows only)
  time_of_day   : train mean log-RV per HH:MM bucket (climatology)

Selection: the operative benchmark for a fold is the candidate with the lowest
QLIKE on the fold's TRAINING rows; that choice is then FIXED for the test fold
and never re-selected using test results.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_state import config as C
from market_state import metrics as M
from market_state.purged_cv import purged_walk_forward_splits


def _tod_key(index, tz: str = C.TIMEZONE) -> pd.Series:
    et = index.tz_convert(tz)
    return pd.Series([t.strftime("%H:%M") for t in et.time], index=index)


# --- parameter-free baselines (defined over the full frame) --------------------

def persistence_var(frame: pd.DataFrame) -> pd.Series:
    return frame["rv_lag6"].copy()


def ewma_var(frame: pd.DataFrame, span: int = C.EWMA_SPAN,
             horizon: int = C.HORIZON_BARS) -> pd.Series:
    sq = frame["sq"].fillna(0.0)     # single session-open bar has no intraday return
    return horizon * sq.ewm(span=span, adjust=False).mean()


# --- fitted baselines (train-only fit) ----------------------------------------

def _har_design(frame: pd.DataFrame) -> np.ndarray:
    cols = [frame["rv_lag6"], frame["rv_lag24"]]
    if C.HAR_USE_PRIOR_SESSION:
        cols.append(frame["rv_prev_session"])
    logs = [np.log(c.where(c > 0)) for c in cols]
    return np.column_stack([l.values for l in logs])


def har_fit_predict(frame: pd.DataFrame, train_mask: np.ndarray) -> pd.Series:
    """Fit HAR OLS of log-RV on log trailing-RV components using TRAIN rows;
    return a full-frame log-RV forecast series (NaN where inputs are invalid)."""
    X = _har_design(frame)
    y = frame["log_rv"].values
    tr = np.asarray(train_mask) & np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    Xtr = np.column_stack([np.ones(tr.sum()), X[tr]])
    beta, *_ = np.linalg.lstsq(Xtr, y[tr], rcond=None)
    valid = np.all(np.isfinite(X), axis=1)
    pred = np.full(len(frame), np.nan)
    Xall = np.column_stack([np.ones(valid.sum()), X[valid]])
    pred[valid] = Xall @ beta
    return pd.Series(pred, index=frame.index)


def climatology_fit_predict(frame: pd.DataFrame, train_mask: np.ndarray) -> pd.Series:
    """Train mean log-RV per HH:MM bucket; predict per bar's bucket (global train
    mean fallback for unseen buckets). Returns a full-frame log-RV forecast."""
    tod = _tod_key(frame.index)
    y = frame["log_rv"]
    tr = pd.Series(np.asarray(train_mask), index=frame.index) & y.notna()
    grp_mean = y[tr].groupby(tod[tr]).mean()
    global_mean = float(y[tr].mean())
    pred = tod.map(grp_mean).astype(float)
    return pred.fillna(global_mean)


# --- v2 Duan smearing for LOG-space baselines ---------------------------------

_LOG_BASELINES = ("har", "time_of_day")


def _log_logpred(frame: pd.DataFrame, name: str, train_mask) -> pd.Series:
    if name == "har":
        return har_fit_predict(frame, train_mask)
    return climatology_fit_predict(frame, train_mask)


def oof_smearing_log_baseline(frame: pd.DataFrame, name: str, train_mask,
                              inner_k: int = C.INNER_K, embargo: int = C.EMBARGO_BARS) -> float:
    """Duan smearing factor for a log-space baseline, from leakage-safe OUT-OF-FOLD
    residuals within the training period only (inner purged walk-forward; the
    baseline is fit on inner-train and scored on held-out inner-val rows)."""
    train_full = np.where(np.asarray(train_mask))[0]
    entry = frame["pos"].values[train_full]
    exit_ = frame["exit_pos"].values[train_full]
    y = frame["log_rv"].values
    resids = []
    for tr, va in purged_walk_forward_splits(entry, exit_, inner_k + 1, embargo):
        inner_tr = np.zeros(len(frame), dtype=bool)
        inner_tr[train_full[tr]] = True
        logpred = _log_logpred(frame, name, inner_tr).values
        va_full = train_full[va]
        r = y[va_full] - logpred[va_full]
        resids.append(r[np.isfinite(r)])
    pooled = np.concatenate(resids) if resids else np.array([])
    return float(np.mean(np.exp(pooled))) if pooled.size else 1.0


# --- assembly + selection ------------------------------------------------------

def all_forecasts(frame: pd.DataFrame, train_mask: np.ndarray,
                  inner_k: int = C.INNER_K, embargo: int = C.EMBARGO_BARS) -> dict:
    """Return {name: {'var','log','s'}} over the full frame. Log-space baselines
    (HAR, time_of_day) carry a v2 OOF smearing factor `s`, so their variance
    forecast is `s·exp(μ̂)`. Persistence and EWMA are variance-space (s=1)."""
    out = {}
    pv = persistence_var(frame)
    out["persistence"] = {"var": pv, "log": np.log(pv.where(pv > 0)), "s": 1.0}
    ev = ewma_var(frame)
    out["ewma"] = {"var": ev, "log": np.log(ev.where(ev > 0)), "s": 1.0}
    for name in _LOG_BASELINES:
        logpred = _log_logpred(frame, name, train_mask)
        s = oof_smearing_log_baseline(frame, name, train_mask, inner_k, embargo)
        out[name] = {"var": s * np.exp(logpred), "log": logpred, "s": s}
    return out


def select_baseline(frame: pd.DataFrame, train_mask: np.ndarray,
                    inner_k: int = C.INNER_K, embargo: int = C.EMBARGO_BARS) -> dict:
    """Choose the candidate with the lowest QLIKE on TRAIN rows (using the
    v2-retransformed variance forecasts for log-space candidates). Returns the
    selected name, per-candidate train QLIKE, and the full-frame forecasts."""
    fc = all_forecasts(frame, train_mask, inner_k, embargo)
    tr = np.asarray(train_mask)
    rv = frame["rv"].values
    train_qlike = {}
    for name, f in fc.items():
        h = f["var"].values
        ok = tr & np.isfinite(rv) & np.isfinite(h) & (rv > 0) & (h > 0)
        train_qlike[name] = M.qlike(rv[ok], h[ok]) if ok.any() else float("inf")
    selected = min(C.CANDIDATE_BASELINES, key=lambda n: train_qlike[n])
    return {"selected": selected, "train_qlike": train_qlike, "forecasts": fc}
