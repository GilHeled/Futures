"""BarBuilder: resample correctness, forming-flag, and causal prefix-stability.

The prefix-stability test is the mandatory no-look-ahead guard (FROZEN_DECISIONS §D):
replaying the 1m stream truncated at any k must reproduce exactly the closed HTF bars the
full stream produced through k.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar
from ict_live.market.bar_builder import BarBuilder

ET = ZoneInfo("America/New_York")


def _min_bars(start_hhmm, n, base=20000.0):
    """n consecutive 1m bars from a fixed ET datetime; deterministic wiggle."""
    y = datetime(2026, 6, 1, start_hhmm[0], start_hhmm[1], tzinfo=ET)
    out = []
    px = base
    for i in range(n):
        o = px
        c = px + (1.0 if i % 2 == 0 else -1.0)
        h = max(o, c) + 0.5
        lo = min(o, c) - 0.5
        ot = y + timedelta(minutes=i)
        out.append(Bar("1m", ot, ot + timedelta(minutes=1), o, h, lo, c, 10.0 + i))
        px = c
    return out


def test_5m_resample_ohlcv_and_times():
    bb = BarBuilder(("5m",))
    bars = _min_bars((9, 30), 5)          # 09:30..09:34 -> one 5m bucket 09:30-09:35
    closed = []
    for b in bars:
        closed += bb.add_1m(b)
    assert len(closed) == 1
    x = closed[0]
    assert x.timeframe == "5m" and not x.forming
    assert x.open_time == datetime(2026, 6, 1, 9, 30, tzinfo=ET)
    assert x.close_time == datetime(2026, 6, 1, 9, 35, tzinfo=ET)
    assert x.open == bars[0].open and x.close == bars[-1].close
    assert x.high == max(b.high for b in bars) and x.low == min(b.low for b in bars)
    assert x.volume == sum(b.volume for b in bars)


def test_forming_bucket_labelled_and_not_emitted():
    bb = BarBuilder(("5m",))
    bars = _min_bars((9, 30), 3)          # only 3 of 5 minutes -> bucket still forming
    closed = []
    for b in bars:
        closed += bb.add_1m(b)
    assert closed == []                    # nothing closed yet
    f = bb.forming("5m")
    assert f is not None and f.forming is True
    assert f.open == bars[0].open and f.close == bars[-1].close


def test_multi_tf_boundaries():
    bb = BarBuilder(("5m", "15m", "1H"))
    bars = _min_bars((9, 0), 60)           # 09:00..09:59 -> 12x5m, 4x15m, 1x1H
    closed = []
    for b in bars:
        closed += bb.add_1m(b)
    by = {}
    for x in closed:
        by.setdefault(x.timeframe, []).append(x)
    assert len(by["5m"]) == 12
    assert len(by["15m"]) == 4
    assert len(by["1H"]) == 1
    assert by["1H"][0].open_time == datetime(2026, 6, 1, 9, 0, tzinfo=ET)
    assert by["1H"][0].close_time == datetime(2026, 6, 1, 10, 0, tzinfo=ET)


def test_prefix_stability_no_lookahead():
    """Closed HTF bars from replaying bars[0..k] == cumulative closed through k (full run)."""
    stream = _min_bars((9, 0), 47)         # arbitrary partial spans of 5m/15m/1H buckets
    tfs = ("5m", "15m", "1H")

    full = BarBuilder(tfs)
    cumulative = []                        # cumulative[k] = closed bars emitted through bar k
    running = []
    for b in stream:
        running += full.add_1m(b)
        cumulative.append(list(running))

    for k in range(len(stream)):
        prefix_bb = BarBuilder(tfs)
        prefix_closed = []
        for b in stream[: k + 1]:
            prefix_closed += prefix_bb.add_1m(b)
        assert prefix_closed == cumulative[k], f"look-ahead/nondeterminism at k={k}"


def test_out_of_order_and_duplicate_ignored():
    bb = BarBuilder(("5m",))
    bars = _min_bars((9, 30), 5)
    for b in bars:
        bb.add_1m(b)
    # duplicate + older bar must not corrupt state (upstream validates; builder is defensive)
    assert bb.add_1m(bars[2]) == []
    assert bb.add_1m(bars[0]) == []
