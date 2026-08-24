"""Volume-roll continuous + causal returns, built locally from dated contracts.

`build_roll` turns a parent panel (all outright contracts) into a daily front/next
series under the frozen **volume roll**: hold the nearest-expiry contract; roll
forward to the next contract the first day its volume exceeds the held contract's
(monotonic — never roll back), skipping expired/untraded contracts. Contracts are
ordered by expiry (proxied by each instrument_id's last trading date, a static,
non-look-ahead property).

Daily return of the held front is computed causally, removing the roll gap via the
next rank (the incoming front was rank 1 the day before):

    non-roll day t:  r_t = front_t / front_{t-1} - 1
    roll day t:      r_t = front_t / next_{t-1} - 1     (front_t == next_{t-1})

r_t uses only t and t-1, so recomputing on any truncation reproduces every past
value exactly (prefix stability — see tests/test_causality.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from trend_carry import config as C
from trend_carry import data as D


# --------------------------------------------------------------------------- #
# Volume-roll construction                                                    #
# --------------------------------------------------------------------------- #
def build_roll(panel: pd.DataFrame) -> pd.DataFrame:
    """Front/next daily series from a parent outright panel.

    Returns a frame indexed by date with front/next close, instrument_id, volume,
    and expiry. `next_*` feeds carry (Phase 2) and the roll-gap correction.
    """
    close = panel.pivot_table(index=panel.index, columns="instrument_id",
                              values="close", aggfunc="last")
    vol = panel.pivot_table(index=panel.index, columns="instrument_id",
                            values="volume", aggfunc="last")
    close = close.sort_index()
    vol = vol.reindex_like(close)

    dates = close.index
    active = close.notna().values
    Cm = close.values
    Vm = np.nan_to_num(vol.values, nan=0.0)
    iids = np.asarray(close.columns)

    T, K = Cm.shape
    last_row = np.array([np.where(active[:, k])[0].max() if active[:, k].any() else -1
                         for k in range(K)])
    order = np.argsort(last_row, kind="stable")   # by expiry proxy ascending
    Cm, Vm, active = Cm[:, order], Vm[:, order], active[:, order]
    iids = iids[order]
    exp_row = last_row[order]
    exp_date = np.array([dates[r] if r >= 0 else pd.NaT for r in exp_row], dtype=object)

    def _next_active(t, start):
        q = start
        while q < K and (not active[t, q] or exp_row[q] < t):
            q += 1
        return q

    fc = np.full(T, np.nan); fv = np.full(T, np.nan)
    nc = np.full(T, np.nan); nv = np.full(T, np.nan)
    fi = np.zeros(T, dtype=np.int64); ni = np.zeros(T, dtype=np.int64)
    fe = np.empty(T, dtype=object); ne = np.empty(T, dtype=object)

    p = 0
    for t in range(T):
        while p < K - 1 and (not active[t, p] or exp_row[p] < t):
            p += 1
        q = _next_active(t, p + 1)
        # volume roll (monotonic): roll into next when it out-volumes the front
        if q < K and active[t, q] and Vm[t, q] > Vm[t, p]:
            p = q
            q = _next_active(t, p + 1)
        fc[t], fv[t], fi[t], fe[t] = Cm[t, p], Vm[t, p], iids[p], exp_date[p]
        if q < K:
            nc[t], nv[t], ni[t], ne[t] = Cm[t, q], Vm[t, q], iids[q], exp_date[q]
        else:
            ni[t] = -1; ne[t] = pd.NaT

    return pd.DataFrame({
        "front_close": fc, "front_iid": fi, "front_vol": fv, "front_exp": fe,
        "next_close": nc, "next_iid": ni, "next_vol": nv, "next_exp": ne,
    }, index=dates)


def _v0v1(roll: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    v0 = pd.DataFrame({"close": roll["front_close"], "instrument_id": roll["front_iid"]})
    v1 = pd.DataFrame({"close": roll["next_close"], "instrument_id": roll["next_iid"]})
    return v0, v1


# --------------------------------------------------------------------------- #
# Causal daily return + adjusted index (unchanged core logic; unit-tested)    #
# --------------------------------------------------------------------------- #
def causal_return_series(v0: pd.DataFrame, v1: pd.DataFrame) -> pd.Series:
    idx = v0.index
    c0 = v0["close"].astype(float)
    id0 = v0["instrument_id"]
    c1 = v1["close"].reindex(idx).astype(float)
    id1 = v1["instrument_id"].reindex(idx)

    same = id0.eq(id0.shift(1))
    r_same = c0 / c0.shift(1) - 1.0
    bridge_ok = (~same) & id1.shift(1).eq(id0) & c1.shift(1).gt(0)
    r_bridge = c0 / c1.shift(1) - 1.0

    r = r_same.where(same, other=np.nan)
    r = r.where(~bridge_ok, other=r_bridge)
    r.iloc[0] = np.nan
    return r


def ratio_adjusted_index(returns: pd.Series) -> pd.Series:
    r = returns.fillna(0.0)
    first = returns.first_valid_index()
    if first is None:
        return pd.Series(index=returns.index, dtype=float)
    idx = (1.0 + r).cumprod()
    return idx / idx.loc[first]


# --------------------------------------------------------------------------- #
# Panel builders over the universe                                            #
# --------------------------------------------------------------------------- #
def build_rolls(roots=C.ROOTS) -> dict[str, pd.DataFrame]:
    return {r: build_roll(D.load_parent(r)) for r in roots}


def build_returns(rolls: dict[str, pd.DataFrame] | None = None, roots=C.ROOTS) -> pd.DataFrame:
    rolls = rolls or build_rolls(roots)
    cols = {}
    for r in roots:
        v0, v1 = _v0v1(rolls[r])
        cols[r] = causal_return_series(v0, v1)
    return pd.DataFrame(cols).sort_index()


def build_adjusted(returns: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({c: ratio_adjusted_index(returns[c]) for c in returns.columns})


def build_front_close(rolls: dict[str, pd.DataFrame] | None = None, roots=C.ROOTS) -> pd.DataFrame:
    rolls = rolls or build_rolls(roots)
    return pd.DataFrame({r: rolls[r]["front_close"] for r in roots}).sort_index()
