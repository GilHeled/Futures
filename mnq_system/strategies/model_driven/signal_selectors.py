"""
Turns a raw, per-bar combination-policy output into a final, non-overlapping
signal calendar -- the fix for "every bar is treated as an independent
opportunity" (see mnq_system.strategies.model_driven.base). Predictions are
serially correlated, so a streak of many consecutive bars whose
`combine_horizon_signals` all agree is really one underlying signal, not
many independent ones; these functions decide which single bar within such
a streak actually gets to trade.

Which selection policy is "right" is itself an untested hypothesis -- per
the project's standing discipline of not assuming an aggregation/selection
rule, all three are built side by side and evaluated identically, letting
the data pick rather than intuition.

Each takes a `raw` DataFrame (columns "direction" [-1/0/+1, NaN before OOS
coverage begins], "strength" [the winning horizon's confidence percentile,
NaN-aligned with direction], "owning_horizon" [the horizon that produced
that bar's winning confidence, NaN-aligned]) and returns a calendar
DataFrame of the same shape/index (columns "direction", "owning_horizon"),
0/NaN everywhere except the selected bars. `direction` is already
threshold-gated by the calling combination policy (mnq_system.strategies.
model_driven.<policy>.combine_horizon_signals uses cfg.confidence_threshold
before ever producing a non-zero direction here) -- these functions only
decide *when*, among an already-qualifying streak, to act.

All three are pure functions over a precomputed Series, independent of
`BacktestEngine`/position state, and all built strictly left-to-right (bar
`j`'s calendar entry only ever depends on bars `<= j`) -- directly
unit-testable and causal by construction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_DEBOUNCE_BARS = 10


def _empty_calendar(index: pd.Index) -> tuple:
    return (
        pd.Series(0.0, index=index, dtype=float),
        pd.Series(np.nan, index=index, dtype=float),
    )


def rising_edge_calendar(raw: pd.DataFrame, debounce_bars: int = DEFAULT_DEBOUNCE_BARS) -> pd.DataFrame:
    """Fires on the first bar a streak's direction differs from the
    previous bar's (a transition into a new non-zero direction, or a
    direct flip from one direction to the other) -- the earliest qualifying
    bar of a streak, not necessarily its strongest. After firing, suppressed
    for the next `debounce_bars` bars regardless of what the signal does
    during them.
    """
    direction, owning_horizon = raw["direction"], raw["owning_horizon"]
    out_direction, out_horizon = _empty_calendar(raw.index)

    prev_direction = 0
    cooldown_until = -1
    for pos in range(len(raw)):
        d = direction.iloc[pos]
        if pd.isna(d):
            prev_direction = 0
            continue
        d = int(d)
        if pos <= cooldown_until:
            prev_direction = d
            continue
        if d != 0 and d != prev_direction:
            out_direction.iloc[pos] = d
            out_horizon.iloc[pos] = owning_horizon.iloc[pos]
            cooldown_until = pos + debounce_bars
        prev_direction = d

    return pd.DataFrame({"direction": out_direction, "owning_horizon": out_horizon})


def peak_confirm_calendar(raw: pd.DataFrame, debounce_bars: int = DEFAULT_DEBOUNCE_BARS) -> pd.DataFrame:
    """Tracks the running max `strength` while a streak's direction stays
    constant; fires on the first bar strength stops increasing (current <=
    the running max), confirming the *previous* bar was the streak's local
    peak -- but executes NOW, at the current (confirming) bar, using the
    streak's direction, since a real system can't retroactively fill at a
    bar that has already closed. A hard reversal (direction flips without
    ever declining first) abandons the old streak unconfirmed rather than
    firing on it. After firing, suppressed for the next `debounce_bars` bars.
    """
    direction, strength, owning_horizon = raw["direction"], raw["strength"], raw["owning_horizon"]
    out_direction, out_horizon = _empty_calendar(raw.index)

    armed_direction = 0
    armed_strength = -np.inf
    cooldown_until = -1
    for pos in range(len(raw)):
        d = direction.iloc[pos]
        if pd.isna(d):
            armed_direction, armed_strength = 0, -np.inf
            continue
        d = int(d)
        s = strength.iloc[pos]

        if pos <= cooldown_until:
            armed_direction, armed_strength = 0, -np.inf
            continue

        if d == 0:
            armed_direction, armed_strength = 0, -np.inf
            continue

        if d != armed_direction:
            armed_direction, armed_strength = d, s  # new streak begins, unconfirmed
            continue

        if s >= armed_strength:
            armed_strength = s  # still rising (or flat) -- keep waiting for a peak
            continue

        # strength just dropped from the peak seen at the previous bar --
        # confirm and act now, at the current bar's own price.
        out_direction.iloc[pos] = armed_direction
        out_horizon.iloc[pos] = owning_horizon.iloc[pos]
        cooldown_until = pos + debounce_bars
        armed_direction, armed_strength = 0, -np.inf

    return pd.DataFrame({"direction": out_direction, "owning_horizon": out_horizon})


def window_max_calendar(raw: pd.DataFrame, window_bars: int = DEFAULT_DEBOUNCE_BARS) -> pd.DataFrame:
    """Chunks the full history into fixed, non-overlapping windows of
    `window_bars` bars (position-based, so window boundaries are known in
    advance without any future data -- live-deployable, not just a backtest
    trick). At each window's own last bar, if any bar within the window
    qualified (direction != 0), fires using the direction/owning_horizon of
    whichever bar in the window had the single highest strength -- executed
    at the window's last bar's current price, not the historical peak
    bar's stale price, at the cost of up to `window_bars` of decision lag.
    """
    direction, strength, owning_horizon = raw["direction"], raw["strength"], raw["owning_horizon"]
    n = len(raw)
    out_direction, out_horizon = _empty_calendar(raw.index)

    start = 0
    while start < n:
        end = min(start + window_bars, n)
        window_direction = direction.iloc[start:end]
        valid = window_direction.notna() & (window_direction != 0)
        if valid.any():
            window_strength = strength.iloc[start:end].where(valid)
            best_label = window_strength.idxmax()
            decision_pos = end - 1
            out_direction.iloc[decision_pos] = int(window_direction.loc[best_label])
            out_horizon.iloc[decision_pos] = owning_horizon.loc[best_label]
        start = end

    return pd.DataFrame({"direction": out_direction, "owning_horizon": out_horizon})


SIGNAL_SELECTORS = {
    "rising_edge": rising_edge_calendar,
    "peak_confirm": peak_confirm_calendar,
    "window_max": window_max_calendar,
}
