"""Causal fractal swing detector: correctness, confirmation lag, prefix-stability."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure.swings import SwingDetector

ET = ZoneInfo("America/New_York")


def _bars(highs, lows):
    """Build closed 1H bars from parallel high/low lists (open/close kept inside range)."""
    t0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)
    out = []
    for i, (h, l) in enumerate(zip(highs, lows)):
        mid = (h + l) / 2
        ot = t0 + timedelta(hours=i)
        out.append(Bar("1H", ot, ot + timedelta(hours=1), mid, h, l, mid, 1.0))
    return out


def test_detects_swing_high_and_low_with_lag():
    # index 2 is a clear high (10) and index 5 a clear low; width=2 => confirm 2 bars later
    highs = [1, 2, 10, 3, 2, 1, 2, 3, 4]
    lows =  [0, 1, 9,  2, 1, 0, 1, 2, 3]
    det = SwingDetector(width=2)
    swings = []
    for i, b in enumerate(_bars(highs, lows)):
        for s in det.add(b):
            swings.append((s.kind, s.index, s.confirm_index, s.price))
    kinds = {(k, idx): (ci, px) for k, idx, ci, px in swings}
    assert ("high", 2) in kinds and kinds[("high", 2)] == (4, 10)   # confirmed at index 4
    assert ("low", 5) in kinds and kinds[("low", 5)][1] == 0        # low price 0 at idx 5


def test_no_confirmation_before_right_window_filled():
    highs = [1, 2, 10, 3, 2]        # high at idx2 needs idx4 to confirm (width=2)
    lows =  [0, 1, 9,  2, 1]
    det = SwingDetector(width=2)
    emitted_before_idx4 = []
    for i, b in enumerate(_bars(highs, lows)):
        got = det.add(b)
        if i < 4:
            emitted_before_idx4 += got
    assert emitted_before_idx4 == []       # nothing knowable until the right neighbors exist


def test_prefix_stability():
    highs = [1, 3, 2, 8, 4, 2, 5, 9, 3, 1, 6, 2, 7, 3, 2]
    lows =  [0, 1, 1, 5, 2, 0, 3, 6, 1, 0, 4, 1, 5, 2, 0]
    bars = _bars(highs, lows)
    full = SwingDetector(2)
    cumulative, running = [], []
    for b in bars:
        running += full.add(b)
        cumulative.append(list(running))
    for k in range(len(bars)):
        pdet = SwingDetector(2)
        pc = []
        for b in bars[: k + 1]:
            pc += pdet.add(b)
        assert pc == cumulative[k], f"nondeterminism/look-ahead at k={k}"


def test_width_one_minor_pivots():
    highs = [1, 5, 2, 6, 3]
    lows =  [0, 4, 1, 5, 2]
    det = SwingDetector(width=1)
    got = []
    for b in _bars(highs, lows):
        got += det.add(b)
    highs_found = sorted(s.index for s in got if s.kind == "high")
    assert highs_found == [1, 3]           # local highs at idx1 and idx3
