"""
Triple-barrier labeling (3-class, direction-agnostic), Topstep-safe.

From each eligible entry bar i: symmetric barriers up=entry+k·ATR,
dn=entry−k·ATR; time barrier = min(i+hold_bars, that day's force-flat bar)
— so a label can NEVER extend past the session cutoff or overnight.

Classes: UP(0)=up-barrier first, DOWN(1)=down-barrier first, TIMEOUT(2)=
neither by the time barrier, AMBIGUOUS(-1)=both barriers touched in the same
5-min bar (order unknown → excluded from training; resolved stop-first in
the backtest, never favorably).

For TIMEOUT rows we record `tret` = signed price return entry→time-barrier
(for a long); the short timeout outcome is exactly −tret. This single causal
quantity is what the 3-outcome EV consumes (see ev.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

UP, DOWN, TIMEOUT, AMBIGUOUS = 0, 1, 2, -1


def label_triple_barrier(high, low, close, atr, entry_eligible, session_exit_pos,
                         k: float, hold_bars: int) -> pd.DataFrame:
    """Vectors are positional (numpy). `session_exit_pos[i]` = index of the
    force-flat bar for bar i's day (the latest bar a trade opened at i may
    still be open). Returns one row per eligible entry with columns:
    entry_pos, exit_pos, label, tret (signed price return to exit, long)."""
    n = len(close)
    rows = []
    for i in range(n):
        if not entry_eligible[i] or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        entry = close[i]
        up = entry + k * atr[i]
        dn = entry - k * atr[i]
        t_barrier = min(i + hold_bars, int(session_exit_pos[i]))
        if t_barrier <= i:
            continue
        label, exit_pos = TIMEOUT, t_barrier
        for t in range(i + 1, t_barrier + 1):
            up_hit = high[t] >= up
            dn_hit = low[t] <= dn
            if up_hit and dn_hit:
                label, exit_pos = AMBIGUOUS, t
                break
            if up_hit:
                label, exit_pos = UP, t
                break
            if dn_hit:
                label, exit_pos = DOWN, t
                break
        tret = close[exit_pos] - entry   # signed price return for a long
        rows.append((i, exit_pos, label, tret))
    return pd.DataFrame(rows, columns=["entry_pos", "exit_pos", "label", "tret"])


def session_exit_positions(force_flat: np.ndarray, et_date: np.ndarray) -> np.ndarray:
    """For each bar, the positional index of its day's force-flat bar (last
    permissible exit). If a day has no explicit force-flat bar, use that
    day's last bar. No trade may be held past this ⇒ no overnight."""
    n = len(force_flat)
    exit_pos = np.full(n, -1, dtype=int)
    # last force-flat (or last) bar per date
    by_date_flat = {}
    by_date_last = {}
    for i in range(n):
        d = et_date[i]
        by_date_last[d] = i
        if force_flat[i]:
            by_date_flat[d] = i
    for i in range(n):
        d = et_date[i]
        exit_pos[i] = by_date_flat.get(d, by_date_last[d])
    return exit_pos
