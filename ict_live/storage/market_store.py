"""Append-only raw 1m storage, keyed by (symbol, bar_open_ms).

Phase 1: an in-memory per-symbol ordered map + optional append-only JSONL on disk (one line
per accepted 1m bar) so a restart can rebuild engine state by replay. Closed bars are NEVER
overwritten: a re-send with identical OHLCV is idempotent; a re-send with DIFFERENT OHLCV is
a conflict the caller logs and rejects (first-write-wins). Storage holds DATA, not policy —
dedupe/ordering/gap DECISIONS live in the Ingestor; this layer only refuses to mutate.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ict_live.market.bar import Bar


def _key(b: Bar) -> int:
    return int(b.open_time.timestamp() * 1000)


class MarketStore:
    def __init__(self, path: Optional[str | Path] = None):
        self._bars: dict[str, dict[int, Bar]] = {}     # symbol -> {open_ms: Bar}
        self._last_ms: dict[str, int] = {}             # symbol -> max open_ms stored
        self._path = Path(path) if path else None
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def get(self, symbol: str, open_ms: int) -> Optional[Bar]:
        return self._bars.get(symbol, {}).get(open_ms)

    def last(self, symbol: str) -> Optional[Bar]:
        ms = self._last_ms.get(symbol)
        return self._bars[symbol][ms] if ms is not None else None

    def bars(self, symbol: str) -> list[Bar]:
        """Chronological (by open time)."""
        d = self._bars.get(symbol, {})
        return [d[k] for k in sorted(d)]

    def count(self, symbol: str) -> int:
        return len(self._bars.get(symbol, {}))

    def append(self, symbol: str, bar: Bar) -> None:
        """Store a 1m bar. Idempotent for an identical re-send; refuses to overwrite a
        stored bar with differing OHLCV (raises so the caller records a conflict)."""
        book = self._bars.setdefault(symbol, {})
        k = _key(bar)
        existing = book.get(k)
        if existing is not None:
            if (existing.open, existing.high, existing.low, existing.close, existing.volume) != \
               (bar.open, bar.high, bar.low, bar.close, bar.volume):
                raise ValueError(f"conflict: differing OHLCV for {symbol}@{k}")
            return                                     # identical re-send -> no-op
        book[k] = bar
        if k > self._last_ms.get(symbol, -1):
            self._last_ms[symbol] = k
        if self._path:
            with self._path.open("a") as fh:
                fh.write(json.dumps({
                    "symbol": symbol, "open_ms": k,
                    "close_ms": int(bar.close_time.timestamp() * 1000),
                    "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close, "v": bar.volume,
                }) + "\n")
