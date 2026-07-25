"""
Target-A labels and label-adjacent quantities, all strictly causal and
intraday-only (no return ever crosses a session boundary).

Everything is computed on the RTH-filtered, session-annotated frame and grouped
by ET date, so forward/backward shifts never leak across the overnight gap.

Primary label (§2):
  RV_t   = sum_{i=1..H} r_{t+i}^2 ,  r = ln(close/close_prev)   (forward realized variance)
  log_rv = ln(RV_t)                                            (model target)
  Dropped unless the full H-bar forward window lies within the same session.

LMP diagnostic event (§10):
  lmp_event_t = 1  iff  max(H-ref, ref-L) over next H bars >= LMP_ATR_MULT * ATR_t
  ref = close_t, direction-agnostic.

Baseline inputs (§4): trailing realized variance at look-backs, prior-session RV.
Robustness (§12): a forward Garman–Klass range-based variance proxy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_state import config as C

_LOG2 = np.log(2.0)


def compute_atr(rth: pd.DataFrame, period: int = C.ATR_PERIOD) -> pd.Series:
    """Wilder ATR on the RTH series. True range uses the WITHIN-SESSION prior
    close (a session's first bar => TR = high-low), so the overnight gap never
    inflates it. EMA state carries across sessions (standard trailing ATR)."""
    g = rth.groupby("et_date", sort=False)
    prev_close = g["close"].shift(1)
    high, low, close = rth["high"], rth["low"], rth["close"]
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    tr = tr.where(prev_close.notna(), high - low)   # first bar of session
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _within_session_log_return(rth: pd.DataFrame) -> pd.Series:
    g = rth.groupby("et_date", sort=False)
    return np.log(rth["close"]) - np.log(g["close"].shift(1))


def _forward_sum(series: pd.Series, rth: pd.DataFrame, horizon: int) -> pd.Series:
    """Sum of `series` over bars t+1..t+horizon within the same session.
    NaN if any term is missing (i.e. the forward window is incomplete)."""
    tmp = rth.assign(_v=series)
    g = tmp.groupby("et_date", sort=False)["_v"]
    total = None
    for m in range(1, horizon + 1):
        shifted = g.shift(-m)
        total = shifted if total is None else total + shifted
    return total


def _trailing_sum(series: pd.Series, rth: pd.DataFrame, window: int) -> pd.Series:
    """Sum of `series` over bars t-window+1..t within the same session
    (includes the current bar's return). NaN until `window` terms exist."""
    tmp = rth.assign(_v=series)
    g = tmp.groupby("et_date", sort=False)["_v"]
    total = None
    for m in range(0, window):
        shifted = g.shift(m)
        total = shifted if total is None else total + shifted
    return total


def _forward_extreme(series: pd.Series, rth: pd.DataFrame, horizon: int, how: str) -> pd.Series:
    tmp = rth.assign(_v=series)
    g = tmp.groupby("et_date", sort=False)["_v"]
    cols = [g.shift(-m) for m in range(1, horizon + 1)]
    mat = pd.concat(cols, axis=1)
    return mat.max(axis=1) if how == "max" else mat.min(axis=1)


def _gk_per_bar(rth: pd.DataFrame) -> pd.Series:
    """Garman–Klass per-bar variance estimator (robustness proxy, §12)."""
    hl = np.log(rth["high"] / rth["low"])
    co = np.log(rth["close"] / rth["open"])
    return 0.5 * hl ** 2 - (2.0 * _LOG2 - 1.0) * co ** 2


def build_label_frame(bars: pd.DataFrame, horizon: int = C.HORIZON_BARS,
                      variance: str = "squared_return") -> pd.DataFrame:
    """From annotated bars (see data.annotate_session), build the RTH-only label
    frame with the forward label, LMP event, ATR, baseline inputs, positional
    indices for purged CV, and a `sample` mask (eligible AND fully-formed).
    """
    rth = bars[bars["in_rth"]].copy()
    rth = rth.sort_index()
    rth["pos"] = np.arange(len(rth))                 # positional index for purged CV

    atr = compute_atr(rth, C.ATR_PERIOD)
    lr = _within_session_log_return(rth)
    sq = lr ** 2
    gk = _gk_per_bar(rth)
    # base per-bar variance measure: squared returns (frozen default) OR the
    # Garman–Klass range estimator (pre-registered §12 robustness alt proxy).
    base = gk if variance == "garman_klass" else sq

    out = pd.DataFrame(index=rth.index)
    out["et_date"] = rth["et_date"].values
    out["pos"] = rth["pos"].values
    out["forecast_eligible"] = rth["forecast_eligible"].values
    out["atr"] = atr

    # --- forward realized variance (primary label) ---
    rv = _forward_sum(base, rth, horizon)
    out["rv"] = rv
    out["log_rv"] = np.log(rv.where(rv > 0))
    out["exit_pos"] = out["pos"] + horizon           # last forward bar (label horizon end)

    # --- forward Garman–Klass variance (robustness alt proxy) ---
    gk_fwd = _forward_sum(gk, rth, horizon)
    out["rv_gk"] = gk_fwd
    out["log_rv_gk"] = np.log(gk_fwd.where(gk_fwd > 0))

    # --- LMP diagnostic event ---
    ref = rth["close"]
    fwd_high = _forward_extreme(rth["high"], rth, horizon, "max")
    fwd_low = _forward_extreme(rth["low"], rth, horizon, "min")
    up_exc = fwd_high - ref
    dn_exc = ref - fwd_low
    max_exc = pd.concat([up_exc, dn_exc], axis=1).max(axis=1)
    window_ok = fwd_high.notna() & fwd_low.notna() & atr.notna() & (atr > 0)
    out["lmp_excursion_atr"] = (max_exc / atr).where(window_ok)
    out["lmp_event"] = (max_exc >= C.LMP_ATR_MULT * atr).where(window_ok).astype("float")

    # --- baseline inputs (trailing realized variance + prior session) ---
    out["rv_lag6"] = _trailing_sum(base, rth, C.HAR_COMPONENT_BARS[0])   # persistence input (30 min)
    out["rv_lag24"] = _trailing_sum(base, rth, C.HAR_COMPONENT_BARS[1])  # ~2 hours
    daily_base = pd.Series(base.values, index=rth["et_date"].values).groupby(level=0).sum()
    prev_session_rv = daily_base.shift(1)
    out["rv_prev_session"] = rth["et_date"].map(prev_session_rv).values
    out["sq"] = base.values                                              # per-bar variance (EWMA baseline)

    # --- sample mask: eligible AND fully-formed label AND baseline inputs present ---
    out["sample"] = (
        out["forecast_eligible"]
        & out["log_rv"].notna()
        & out["rv_lag6"].notna()
        & out["rv_lag24"].notna()
        & out["rv_prev_session"].notna()
        & (out["rv_prev_session"] > 0)
        & out["atr"].notna()
    )
    return out
