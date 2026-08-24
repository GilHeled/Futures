"""Signals. Phase 1: trend (time-series momentum) only — frozen §4, §12.5.

Trend = equal-weight ensemble of sign(cumulative return) over the canonical
lookbacks {21,63,126,252} trading days, on the ratio-adjusted index. The signal
at day t uses information through t's close; the backtest applies it to t+1's
return (see backtest.py), so it is causal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from trend_carry import config as C


def trend_signal(adjusted: pd.DataFrame, lookbacks=C.TREND_LOOKBACKS) -> pd.DataFrame:
    """Ensemble trend signal in [-1, 1], one column per root."""
    acc = None
    for L in lookbacks:
        mom = adjusted / adjusted.shift(L) - 1.0
        s = np.sign(mom)
        acc = s if acc is None else acc + s
    return acc / float(len(lookbacks))
