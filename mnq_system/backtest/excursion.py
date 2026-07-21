"""
Maximum Favorable/Adverse Excursion (MFE/MAE) and near-miss analysis --
diagnostic tools for distinguishing "the stop is too tight for the current
volatility regime" from "the edge itself disappeared." See
docs/SPEC.md's verification workflow. Neither of these functions is an
entry/exit rule; they only ever look at bars already in hand, for
after-the-fact analysis of trades a backtest already produced.
"""

from __future__ import annotations

import pandas as pd


def compute_trade_excursion(
    bars: pd.DataFrame, direction: str, entry_price: float, entry_time: pd.Timestamp, exit_time: pd.Timestamp
) -> dict:
    """MFE (best it ever moved in your favor) and MAE (worst it ever moved
    against you) in raw price points, using every bar's high/low from
    `entry_time` to `exit_time` inclusive. Both are >= 0 by construction --
    "favorable"/"adverse" already account for direction.
    """
    window = bars.loc[(bars.index >= entry_time) & (bars.index <= exit_time)]
    if window.empty:
        return {"mfe": 0.0, "mae": 0.0}
    if direction == "long":
        mfe = (window["high"] - entry_price).max()
        mae = (entry_price - window["low"]).max()
    else:
        mfe = (entry_price - window["low"]).max()
        mae = (window["high"] - entry_price).max()
    return {"mfe": float(max(mfe, 0.0)), "mae": float(max(mae, 0.0))}


def near_miss_after_stop(
    bars: pd.DataFrame,
    direction: str,
    entry_price: float,
    target_1: float,
    stop_time: pd.Timestamp,
    lookahead_end: pd.Timestamp,
) -> dict:
    """Purely hypothetical: the position already closed at `stop_time`, so
    none of this affects any P&L -- it only asks whether price would have
    gone on to reach 50/75/100% of the original target_1 distance before
    `lookahead_end`, had the stop not been there. A high hit rate here is
    evidence the market kept moving through noise (the stop was too tight
    for the move it was trying to catch); a low hit rate means the reversal
    genuinely failed and volatility alone wouldn't have saved the trade.
    """
    window = bars.loc[(bars.index > stop_time) & (bars.index <= lookahead_end)]
    target_distance = abs(target_1 - entry_price)
    if window.empty or target_distance <= 0:
        return {"reached_50pct": False, "reached_75pct": False, "reached_100pct": False, "best_pct_of_target": 0.0}

    if direction == "long":
        best_favorable = (window["high"] - entry_price).max()
    else:
        best_favorable = (entry_price - window["low"]).max()
    best_favorable = max(float(best_favorable), 0.0)
    pct = best_favorable / target_distance

    return {
        "reached_50pct": pct >= 0.5,
        "reached_75pct": pct >= 0.75,
        "reached_100pct": pct >= 1.0,
        "best_pct_of_target": float(pct),
    }
