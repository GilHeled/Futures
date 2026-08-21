"""Feed abstraction: a Feed turns a transport payload into a validated 1m Bar + symbol.

The strategy brain never sees a feed. Everything downstream consumes `ParsedBar`, so the
transport (TradingView webhook now; IB/Databento later) is swappable without touching the
engine. Parsing is pure and total: it either returns a ParsedBar or raises FeedError(reason);
it performs NO storage, ordering, or gap logic (that is the Ingestor's job).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ict_live.market.bar import Bar


class FeedError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ParsedBar:
    symbol: str          # raw feed symbol, e.g. "CME_MINI:NQ1!"
    bar: Bar             # 1m Bar, tz-aware ET


class Feed(Protocol):
    def parse(self, payload: dict) -> ParsedBar: ...
