"""Risk-scaled, cost-aware portfolio backtest — frozen §5, §8.

Per instrument i, day t (causal):
    vol_{i,t}      = trailing VOL_LOOKBACK std of daily returns (through t-1)
    contracts_{i,t}= signal_{i,t} / (vol_{i,t} * price_{i,t} * point_value_i)
                     (risk-normalized to ~equal dollar vol per instrument;
                      the overall risk unit cancels out of net Sharpe)
    pnl$_{i,t}     = contracts_{i,t-1} * return_{i,t} * price_{i,t-1} * pv_i
                     = signal_{i,t-1} * return_{i,t} / vol_{i,t-1}
    cost$_{i,t}    = |contracts_{i,t} - contracts_{i,t-1}| * cost_per_side_i

Net Sharpe is invariant to the arbitrary risk unit (both pnl and cost scale with
it), so we set it to 1. The VOL_TARGET only rescales the reported equity curve /
drawdown, never the Sharpe or the sign of the mean.

`net_inst` is returned per instrument so any subset (a sector, a drop-best set)
is just a column-sum — used by the breadth tests.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trend_carry import config as C


@dataclass
class BacktestResult:
    net_inst: pd.DataFrame     # dates x roots, net-of-cost contribution (risk units)
    gross_inst: pd.DataFrame   # dates x roots, gross contribution
    cost_inst: pd.DataFrame    # dates x roots, cost drag
    contracts: pd.DataFrame    # dates x roots, position in contracts (risk unit=1)

    @property
    def net(self) -> pd.Series:
        return self.net_inst.sum(axis=1)

    @property
    def gross(self) -> pd.Series:
        return self.gross_inst.sum(axis=1)

    def window(self, start, end) -> "BacktestResult":
        s = pd.Timestamp(start, tz="UTC"); e = pd.Timestamp(end, tz="UTC")
        m = (self.net_inst.index >= s) & (self.net_inst.index <= e)
        return BacktestResult(self.net_inst[m], self.gross_inst[m],
                              self.cost_inst[m], self.contracts[m])


def run_backtest(returns: pd.DataFrame, signals: pd.DataFrame,
                 front_close: pd.DataFrame, roots=C.ROOTS,
                 cost_mult: float = 1.0, vol_lookback: int = C.VOL_LOOKBACK
                 ) -> BacktestResult:
    roots = list(roots)
    idx = returns.index
    pv = np.array([C.BY_ROOT[r].point_value for r in roots])
    cost_side = np.array([C.cost_per_side_dollars(r, cost_mult) for r in roots])

    R = returns[roots].reindex(idx).values.astype(float)
    S = signals[roots].reindex(idx).values.astype(float)
    P = front_close[roots].reindex(idx).values.astype(float)
    V = returns[roots].rolling(vol_lookback).std().reindex(idx).values.astype(float)

    # position (contracts) held from t into t+1; risk unit = 1
    with np.errstate(divide="ignore", invalid="ignore"):
        contracts = S / (V * P * pv)
    contracts = np.where(np.isfinite(contracts), contracts, 0.0)

    Rf = np.where(np.isfinite(R), R, 0.0)
    Pf = np.where(np.isfinite(P), P, np.nan)

    n = len(idx)
    gross = np.zeros((n, len(roots)))
    cost = np.zeros((n, len(roots)))
    # day k (k>=1): pnl from position held at k-1, price move k-1 -> k
    gross[1:] = contracts[:-1] * Rf[1:] * np.nan_to_num(Pf[:-1], nan=0.0) * pv
    cost[1:] = np.abs(contracts[1:] - contracts[:-1]) * cost_side
    net = gross - cost

    return BacktestResult(
        net_inst=pd.DataFrame(net, index=idx, columns=roots),
        gross_inst=pd.DataFrame(gross, index=idx, columns=roots),
        cost_inst=pd.DataFrame(cost, index=idx, columns=roots),
        contracts=pd.DataFrame(contracts, index=idx, columns=roots),
    )
