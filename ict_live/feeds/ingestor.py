"""Framework-agnostic ingestion pipeline. FastAPI (api/webhook.py) is a thin wrapper; this
class contains ALL the decision logic so it is unit-testable without a web server and so the
identical path serves live webhooks and historical replay (live == backtest).

For each raw payload, in order (DATA_FEED_SPEC §3), every outcome logged to the event trail:
  auth -> parse -> symbol routing -> dedupe/conflict -> ordering -> gap -> persist -> resample.

Returns IngestResult; never raises for a bad message (bad messages are rejected + logged).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ict_live import config as C
from ict_live.feeds.base import Feed, FeedError
from ict_live.feeds.tradingview_webhook import TradingViewWebhookFeed
from ict_live.market.bar import Bar
from ict_live.market.bar_builder import BarBuilder
from ict_live.market.calendar import Calendar
from ict_live.storage.event_trail import EventTrail
from ict_live.storage.market_store import MarketStore

# terminal statuses
ACCEPTED, REJECTED, DUPLICATE, CONFLICT, OUT_OF_ORDER = \
    "accepted", "rejected", "duplicate", "conflict", "out_of_order"


@dataclass
class IngestResult:
    status: str
    reason: Optional[str] = None
    symbol: Optional[str] = None
    closed_htf: list[Bar] = field(default_factory=list)
    gap_minutes: int = 0


def _open_ms(b: Bar) -> int:
    return int(b.open_time.timestamp() * 1000)


class Ingestor:
    def __init__(self, *, token: Optional[str] = None, feed: Optional[Feed] = None,
                 store: Optional[MarketStore] = None, trail: Optional[EventTrail] = None,
                 calendar: Optional[Calendar] = None,
                 timeframes=("5m", "15m", "1H", "4H", "D", "W")):
        self.token = token                       # None => auth disabled (tests/local)
        self.feed = feed or TradingViewWebhookFeed()
        self.store = store or MarketStore()
        self.trail = trail or EventTrail()
        self.cal = calendar or Calendar()
        self._timeframes = tuple(timeframes)
        self._builders: dict[str, BarBuilder] = {}

    def _builder(self, symbol: str) -> BarBuilder:
        bb = self._builders.get(symbol)
        if bb is None:
            bb = BarBuilder(self._timeframes, calendar=self.cal)
            self._builders[symbol] = bb
        return bb

    def ingest(self, payload: dict, *, token: Optional[str] = None) -> IngestResult:
        # 1. auth
        if self.token is not None and token != self.token:
            self.trail.log("rejected", reason="auth")
            return IngestResult(REJECTED, reason="auth")

        # 2. parse
        try:
            parsed = self.feed.parse(payload)
        except FeedError as e:
            self.trail.log("rejected", reason=e.reason)
            return IngestResult(REJECTED, reason=e.reason)
        symbol, bar = parsed.symbol, parsed.bar
        k = _open_ms(bar)

        # 3. symbol routing
        if symbol not in C.INSTRUMENTS:
            self.trail.log("ignored", reason="unknown_symbol", symbol=symbol)
            return IngestResult(REJECTED, reason="unknown_symbol", symbol=symbol)

        # 4. dedupe / conflict
        existing = self.store.get(symbol, k)
        if existing is not None:
            same = (existing.open, existing.high, existing.low, existing.close, existing.volume) \
                == (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if same:
                self.trail.log("duplicate", symbol=symbol, open_ms=k)
                return IngestResult(DUPLICATE, symbol=symbol)
            self.trail.log("conflict", symbol=symbol, open_ms=k)   # first-write-wins
            return IngestResult(CONFLICT, reason="ohlcv_mismatch", symbol=symbol)

        # 5. ordering — strictly increasing per symbol
        last = self.store.last(symbol)
        if last is not None and k < _open_ms(last):
            self.store.append(symbol, bar)                          # store in place
            self.trail.log("out_of_order", symbol=symbol, open_ms=k,
                           note="downstream_recompute_needed")
            return IngestResult(OUT_OF_ORDER, symbol=symbol)

        # 6. gap detection (only for the newest bar of the symbol)
        gap = 0
        if last is not None:
            expected = self.cal.next_expected_open_minute(last.close_time)
            if bar.open_time > expected:
                gap = self.cal.count_open_minutes(expected, bar.open_time)
                if gap > 0:
                    self.trail.log("missing_bars", symbol=symbol, n=gap,
                                   from_ms=int(expected.timestamp() * 1000), to_ms=k)

        # 7. persist raw (append-only)
        self.store.append(symbol, bar)

        # 8. resample -> newly closed higher-TF bars
        closed = self._builder(symbol).add_1m(bar)

        self.trail.log("accepted", symbol=symbol, open_ms=k,
                       closed_htf=[b.timeframe for b in closed], gap_minutes=gap)
        return IngestResult(ACCEPTED, symbol=symbol, closed_htf=closed, gap_minutes=gap)

    # ---- introspection for /status ----
    def status(self) -> dict:
        return {
            "symbols": {
                s: {
                    "bars_1m": self.store.count(s),
                    "last_open_ms": (_open_ms(self.store.last(s)) if self.store.last(s) else None),
                    "last_close": (self.store.last(s).close if self.store.last(s) else None),
                    "forming": {tf: (self._builders[s].forming(tf) is not None)
                                for tf in self._timeframes} if s in self._builders else {},
                }
                for s in {**{k: 1 for k in self._builders}, **{k: 1 for k in C.INSTRUMENTS
                          if self.store.count(k)}}
            },
            "events": len(self.trail.events()),
        }
