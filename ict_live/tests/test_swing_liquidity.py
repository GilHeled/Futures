"""Swing-derived liquidity: active (unswept) vs swept, decided causally by later wicks."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.structure.swing_liquidity import active, swept, swing_liquidity
from ict_live.structure.swings import Swing

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 6, 1, 9, 0, tzinfo=ET)


def _bar(i, hi, lo):
    ot = T0 + timedelta(hours=i)
    mid = (hi + lo) / 2
    return Bar("1H", ot, ot + timedelta(hours=1), mid, hi, lo, mid, 1.0)


def _sw(kind, idx, price):
    return Swing(kind, idx, idx + 2, T0 + timedelta(hours=idx), price)


def test_high_pool_swept_by_later_wick():
    bars = [_bar(0, 20, 10), _bar(1, 25, 15), _bar(2, 30, 20)]   # idx2 high 30 > 25
    pools = swing_liquidity([_sw("high", 1, 25)], bars)
    assert pools[0].swept is True and pools[0].swept_index == 2


def test_low_pool_active_until_wick_breaks():
    bars = [_bar(0, 20, 10), _bar(1, 18, 8), _bar(2, 19, 9)]     # nothing dips below 8
    pools = swing_liquidity([_sw("low", 1, 8)], bars)
    assert pools[0].swept is False and pools[0].swept_index is None
    assert active(pools) and not swept(pools)


def test_sweep_is_causal_only_later_bars_count():
    # a big wick BEFORE the swing must not sweep it
    bars = [_bar(0, 99, 0), _bar(1, 20, 10), _bar(2, 21, 11)]
    pools = swing_liquidity([_sw("high", 1, 20)], bars)         # idx0 wick 99 precedes -> ignored
    assert pools[0].swept is True and pools[0].swept_index == 2  # idx2 high 21 > 20 sweeps it


def test_active_and_swept_partition():
    bars = [_bar(i, 20 + i, 10 - i) for i in range(6)]           # highs rise, lows fall
    swings = [_sw("high", 1, 21), _sw("low", 1, 9), _sw("high", 5, 25)]
    pools = swing_liquidity(swings, bars)
    assert len(active(pools)) + len(swept(pools)) == len(pools)
