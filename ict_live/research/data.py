"""Offline historical bar loading + deterministic resampling for the ML dataset (research only).

Reads the existing cached RAW Databento 5-minute bars (cache/bars/) — never any derived backtest
signal — and feeds them through the SAME shape the live engine expects (ict_live Bar, ET time).
5-minute is the finest granularity on disk and is finer than the engine's analysis TFs (1H/15m),
so it resamples up causally and gives better-than-signal outcome resolution.

This module lives under research/ (not the live engine) because it imports pandas and is offline;
the live market/structure/engine packages stay dependency-light. Prices are RAW and non-adjusted —
roll handling is in research/rolls.py, never a silent splice here.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from ict_live.market.bar import Bar

ET = ZoneInfo("America/New_York")
CACHE = Path(__file__).resolve().parents[2] / "cache" / "bars"
_MIN = {"15m": 15, "1H": 60, "4H": 240}


def load_5m(symbol: str, *, source: str = "databento",
            start: Optional[str] = None, end: Optional[str] = None) -> list[Bar]:
    """Load cached 5-minute bars for `symbol` as ET-timed ict_live Bars (chronological).
    `start`/`end` are ISO dates interpreted in UTC (inclusive)."""
    import pandas as pd
    df = pd.read_parquet(CACHE / f"{source}_{symbol}_5m.parquet")
    if start is not None:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
    bars: list[Bar] = []
    for ts, row in df.iterrows():
        ot = ts.tz_convert(ET).to_pydatetime()
        bars.append(Bar("5m", ot, ot + timedelta(minutes=5),
                        float(row["open"]), float(row["high"]), float(row["low"]),
                        float(row["close"]), float(row["volume"])))
    return bars


def _bucket_start(ot: datetime, m: int) -> datetime:
    et = ot.astimezone(ET)
    if m >= 60:
        fh = (et.hour // (m // 60)) * (m // 60)
        return datetime(et.year, et.month, et.day, fh, 0, tzinfo=ET)
    tot = et.hour * 60 + et.minute
    fl = (tot // m) * m
    return datetime(et.year, et.month, et.day, fl // 60, fl % 60, tzinfo=ET)


def resample(bars: list[Bar], tf: str) -> list[Bar]:
    """Deterministic ET-clock resample of finer bars up to `tf` (15m/1H/4H). Causal: a bucket is
    emitted when a later bucket begins (gap-aware). The trailing bucket is emitted at the end."""
    m = _MIN[tf]
    out: list[Bar] = []
    cur = None
    for b in bars:
        s = _bucket_start(b.open_time, m)
        if cur is not None and s > cur["start"]:
            out.append(Bar(tf, cur["start"], cur["end"], cur["o"], cur["h"], cur["l"], cur["c"], cur["v"]))
            cur = None
        if cur is None:
            cur = {"start": s, "end": s + timedelta(minutes=m),
                   "o": b.open, "h": b.high, "l": b.low, "c": b.close, "v": b.volume}
        else:
            cur["h"] = max(cur["h"], b.high)
            cur["l"] = min(cur["l"], b.low)
            cur["c"] = b.close
            cur["v"] += b.volume
    if cur is not None:
        out.append(Bar(tf, cur["start"], cur["end"], cur["o"], cur["h"], cur["l"], cur["c"], cur["v"]))
    return out
