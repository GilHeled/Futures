"""
Shared frozen-model inference: the SINGLE function that turns a bar frame
into per-bar EV, direction signal (rising_edge over EV-vs-cost-hurdle), and
off-hours flags. Both the batch reference and the streaming engine call
this exact function, so streaming-vs-batch parity holds by construction
(the function is causal -- each row depends only on that row's features and
past bars via finite-lookback indicators/debounce).
"""
from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from live_validation.bundle import ATR_PERIOD, FrozenBundle
from mnq_system.indicators import atr
from mnq_system.modeling.features import DEFAULT_FEATURE_CONFIG, build_feature_matrix
from mnq_system.strategies.model_driven.signal_selectors import DEFAULT_DEBOUNCE_BARS, SIGNAL_SELECTORS


def _align_proba(clf, X, classes) -> np.ndarray:
    """Same column remap as evaluate._align_proba: predict_proba columns are
    in clf.classes_ order; project onto the fixed `classes` order."""
    raw = clf.predict_proba(X)
    out = np.zeros((len(X), len(classes)), dtype=float)
    for i, c in enumerate(clf.classes_):
        j = int(np.searchsorted(classes, c))
        out[:, j] = raw[:, i]
    return out


def _within_windows(t: dt_time, windows) -> bool:
    for sh, sm, eh, em in windows:
        if dt_time(sh, sm) <= t < dt_time(eh, em):
            return True
    return False


def session_flags(index: pd.DatetimeIndex, account):
    """entry_allowed & session_ending exactly as BacktestEngine computes
    them; off_hours = not (entry_allowed & ~session_ending)."""
    tz = account.session.timezone
    et = index.tz_convert(tz)
    n = len(index)
    entry_allowed = np.zeros(n, dtype=bool)
    session_ending = np.zeros(n, dtype=bool)
    end_h, end_m = account.session.trading_windows[-1][2], account.session.trading_windows[-1][3]
    interval = index[1] - index[0] if n > 1 else pd.Timedelta(minutes=5)
    for t in range(n):
        e = et[t]
        entry_allowed[t] = (_within_windows(e.time(), account.session.trading_windows)
                            or _within_windows(e.time(), account.session.reduced_size_windows))
        # CLOCK-BASED only (causal): a bar is session-ending if its own close
        # reaches the day's session end. The backtest additionally used the
        # NEXT bar's date as a fallback, but that is a lookahead the live
        # frontier bar can't have -- and dropping it is immaterial to the
        # off-hours population (the extra bars it flagged are outside the
        # entry windows anyway). Verified by the streaming==batch parity test.
        end_today = e.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        session_ending[t] = (e + interval) >= end_today
    off_hours = ~(entry_allowed & (~session_ending))
    return entry_allowed, session_ending, off_hours


def compute_frame(bars: pd.DataFrame, bundle: FrozenBundle, account,
                  debounce_bars: int = DEFAULT_DEBOUNCE_BARS) -> pd.DataFrame:
    """Per-bar EV / cost_hurdle / raw+selected direction / session flags for
    `bars` under the frozen bundle. Causal: row t uses only features at t
    (finite indicator lookback) and the rising_edge debounce over past bars."""
    features = build_feature_matrix({"entry": bars}, account, DEFAULT_FEATURE_CONFIG)
    features = features[bundle.feature_columns]
    atr_series = atr(bars, period=ATR_PERIOD)

    valid = features.notna().all(axis=1)
    ev = pd.Series(np.nan, index=bars.index)
    if valid.any():
        proba = _align_proba(bundle.classifier, features.loc[valid], bundle.classes)
        ev.loc[valid] = proba @ bundle.class_return_vec

    atr_dollars = atr_series * bundle.point_value
    cost_hurdle = bundle.round_trip_cost_dollars / atr_dollars.where(atr_dollars > 0)

    both_valid = ev.notna() & cost_hurdle.notna()
    raw_dir = pd.Series(np.nan, index=bars.index)
    raw_dir.loc[both_valid] = 0.0
    raw_dir.loc[both_valid & (ev > cost_hurdle)] = 1.0
    raw_dir.loc[both_valid & (ev < -cost_hurdle)] = -1.0

    strength = pd.Series(np.nan, index=bars.index)
    strength.loc[both_valid] = ev.loc[both_valid].abs()
    owning = pd.Series(np.nan, index=bars.index)
    owning.loc[both_valid] = 1
    raw = pd.DataFrame({"direction": raw_dir, "strength": strength, "owning_horizon": owning})
    calendar = SIGNAL_SELECTORS["rising_edge"](raw, debounce_bars)

    entry_allowed, session_ending, off_hours = session_flags(bars.index, account)

    return pd.DataFrame({
        "ev": ev,
        "atr": atr_series,
        "cost_hurdle": cost_hurdle,
        "direction": calendar["direction"],
        "entry_allowed": entry_allowed,
        "session_ending": session_ending,
        "off_hours": off_hours,
    }, index=bars.index)
