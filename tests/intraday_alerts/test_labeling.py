import numpy as np

from intraday_alerts.labeling import (
    AMBIGUOUS, DOWN, TIMEOUT, UP, label_triple_barrier, session_exit_positions,
)


def _base(n=10):
    close = np.full(n, 100.0)
    high = close.copy()
    low = close.copy()
    atr = np.full(n, 1.0)
    entry_eligible = np.zeros(n, dtype=bool)
    entry_eligible[0] = True
    session_exit_pos = np.full(n, n - 1)   # all one day, last bar = force-flat
    return high, low, close, atr, entry_eligible, session_exit_pos


def test_up_first():
    h, l, c, atr, elig, sx = _base()
    h[1] = 101.0                       # up-barrier = 100 + 1*1
    df = label_triple_barrier(h, l, c, atr, elig, sx, k=1.0, hold_bars=6)
    assert df.iloc[0]["label"] == UP and df.iloc[0]["exit_pos"] == 1


def test_down_first():
    h, l, c, atr, elig, sx = _base()
    l[1] = 99.0
    df = label_triple_barrier(h, l, c, atr, elig, sx, k=1.0, hold_bars=6)
    assert df.iloc[0]["label"] == DOWN and df.iloc[0]["exit_pos"] == 1


def test_double_touch_is_ambiguous():
    h, l, c, atr, elig, sx = _base()
    h[1], l[1] = 101.0, 99.0           # both barriers in one bar -> order unknown
    df = label_triple_barrier(h, l, c, atr, elig, sx, k=1.0, hold_bars=6)
    assert df.iloc[0]["label"] == AMBIGUOUS


def test_timeout_and_tret_sign():
    h, l, c, atr, elig, sx = _base()
    c[6] = 100.5                       # never touches barriers; exits at hold barrier (i+6)
    df = label_triple_barrier(h, l, c, atr, elig, sx, k=1.0, hold_bars=6)
    row = df.iloc[0]
    assert row["label"] == TIMEOUT and row["exit_pos"] == 6
    assert np.isclose(row["tret"], 0.5)   # signed price return for a long


def test_time_barrier_respects_session_cutoff_no_overnight():
    h, l, c, atr, elig, sx = _base(n=10)
    sx[:] = 3                          # force-flat at bar 3 (earlier than hold_bars=6)
    df = label_triple_barrier(h, l, c, atr, elig, sx, k=1.0, hold_bars=6)
    assert df.iloc[0]["exit_pos"] <= 3     # never held past the day's cutoff


def test_session_exit_positions():
    force_flat = np.array([False, False, True, False, False, True])
    et_date = np.array([1, 1, 1, 2, 2, 2])
    ex = session_exit_positions(force_flat, et_date)
    assert list(ex) == [2, 2, 2, 5, 5, 5]
