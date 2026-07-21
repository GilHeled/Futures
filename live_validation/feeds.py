"""
Pluggable live-feed adapters. Each adapter connects to a real-time source
and yields a NORMALIZED event stream that the (already-validated,
vendor-agnostic) shadow consumer in run_shadow.py drives the model + book
from. Adding a vendor = adding one adapter here; nothing downstream changes.

Normalized events (all carry the resolved underlying-contract identity so
rollovers are explicit and bar/quote can be checked for contract match):
  - MappingEvent: instrument_id -> (symbol, raw_contract) resolved/rolled
  - QuoteEvent:   latest top-of-book bid/ask for a symbol
  - BarEvent:     a completed 5-minute OHLCV bar for a symbol

Adapters:
  - DatabentoLiveFeed: Databento live (GLBX.MDP3), the original path.
  - IBKRLiveFeed: Interactive Brokers via ib_async (TWS / IB Gateway) --
    the cheap real-time CME route that doubles as the execution broker.
    Requires TWS or IB Gateway running and a CME real-time data subscription.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Optional

import pandas as pd

# Databento continuous front-month symbology (calendar roll).
DB_CONTINUOUS = {"MYM": "MYM.c.0", "M2K": "M2K.c.0", "MES": "MES.c.0"}


@dataclass
class MappingEvent:
    symbol: str
    instrument_id: int
    raw_symbol: Optional[str]
    kind: str = "mapping"


@dataclass
class QuoteEvent:
    symbol: str
    instrument_id: Optional[int]
    raw_symbol: Optional[str]
    bid: Optional[float]
    ask: Optional[float]
    kind: str = "quote"


@dataclass
class BarEvent:
    symbol: str
    instrument_id: Optional[int]
    raw_symbol: Optional[str]
    ts: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    kind: str = "bar"


class LiveFeed(ABC):
    @abstractmethod
    def stream(self) -> Iterator[object]:
        """Yield MappingEvent / QuoteEvent / BarEvent until stopped."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Databento
# ---------------------------------------------------------------------------

class DatabentoLiveFeed(LiveFeed):
    def __init__(self, symbols, dataset="GLBX.MDP3", scale=1e9):
        self.symbols = symbols
        self.dataset = dataset
        self.scale = scale

    def stream(self):
        import databento as db

        key = os.environ.get("DATABENTO_API_KEY")
        if not key:
            raise SystemExit("DATABENTO_API_KEY not set in environment.")
        cont_to_sym = {DB_CONTINUOUS[s]: s for s in self.symbols}
        id_to_symbol, id_to_raw = {}, {}
        conts = [DB_CONTINUOUS[s] for s in self.symbols]

        client = db.Live(key=key)
        client.subscribe(dataset=self.dataset, schema="mbp-1", stype_in="continuous", symbols=conts)
        client.subscribe(dataset=self.dataset, schema="ohlcv-5m", stype_in="continuous", symbols=conts)

        for record in client:
            if hasattr(record, "stype_out_symbol") and hasattr(record, "instrument_id"):
                iid = record.instrument_id
                sym = cont_to_sym.get(getattr(record, "stype_in_symbol", None))
                if sym is not None:
                    id_to_symbol[iid] = sym
                    id_to_raw[iid] = getattr(record, "stype_out_symbol", None)
                    yield MappingEvent(sym, iid, id_to_raw[iid])
                continue
            iid = getattr(record, "instrument_id", None)
            sym = id_to_symbol.get(iid)
            if sym is None:
                continue
            raw = id_to_raw.get(iid)
            if hasattr(record, "levels") and record.levels:
                lvl = record.levels[0]
                yield QuoteEvent(sym, iid, raw, lvl.bid_px / self.scale, lvl.ask_px / self.scale)
            elif all(hasattr(record, f) for f in ("open", "high", "low", "close")):
                yield BarEvent(sym, iid, raw, pd.Timestamp(record.ts_event, unit="ns", tz="UTC"),
                               record.open / self.scale, record.high / self.scale,
                               record.low / self.scale, record.close / self.scale,
                               getattr(record, "volume", 0))


# ---------------------------------------------------------------------------
# Interactive Brokers (ib_async / TWS / IB Gateway)
# ---------------------------------------------------------------------------

class IBKRLiveFeed(LiveFeed):
    """Interactive Brokers real-time feed via ib_async. Uses ONE concrete
    front-month future per symbol for both the 5m bars and the top-of-book,
    so a bar and its quote always share the same contract (conId) -- the
    contract-consistency check is satisfied by construction, and a rollover
    surfaces as a new conId (re-resolved on reconnect / restart).

    Requirements (all on the user's side): TWS or IB Gateway running and
    logged in, API enabled, and a CME real-time market-data subscription.
    From Docker, point --ib-host at host.docker.internal.

    5m bars come from reqHistoricalData(keepUpToDate=True) (IB streams a
    completed bar per interval); BBO from reqMktData (ticker.bid/ask).
    """

    def __init__(self, symbols, host="127.0.0.1", port=7497, client_id=17, exchange="CME"):
        self.symbols = symbols
        self.host = host
        self.port = port
        self.client_id = client_id
        self.exchange = exchange

    def _resolve_front(self, ib, sym):
        """Pick the nearest non-expired monthly contract as the tradable
        front month (a concrete Future with conId + localSymbol), rather
        than a synthetic continuous, so identity/rollover are real."""
        from ib_async import Future

        details = ib.reqContractDetails(Future(sym, exchange=self.exchange, includeExpired=False))
        contracts = sorted(
            (d.contract for d in details),
            key=lambda c: c.lastTradeDateOrContractMonth,
        )
        today = pd.Timestamp.utcnow().strftime("%Y%m%d")
        front = next((c for c in contracts if c.lastTradeDateOrContractMonth >= today), contracts[0])
        ib.qualifyContracts(front)
        return front

    def stream(self):
        import queue

        from ib_async import IB

        ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id)
        q: "queue.Queue" = queue.Queue()

        contracts, tickers = {}, {}
        conid_to = {}
        for sym in self.symbols:
            c = self._resolve_front(ib, sym)
            contracts[sym] = c
            conid_to[c.conId] = (sym, c.localSymbol)
            q.put(MappingEvent(sym, c.conId, c.localSymbol))
            # streaming top-of-book
            tickers[sym] = ib.reqMktData(c, "", False, False)
            # streaming 5m bars (historical seed + live updates)
            bars = ib.reqHistoricalData(
                c, endDateTime="", durationStr="1 D", barSizeSetting="5 mins",
                whatToShow="TRADES", useRTH=False, formatDate=2, keepUpToDate=True,
            )

            def _on_bar_update(bars_, has_new_bar, _sym=sym, _c=c):
                if not has_new_bar or len(bars_) < 2:
                    return
                b = bars_[-2]  # -1 is the still-forming bar; -2 is the just-completed one
                tk = tickers[_sym]
                if tk.bid is not None and tk.ask is not None:
                    q.put(QuoteEvent(_sym, _c.conId, _c.localSymbol, float(tk.bid), float(tk.ask)))
                q.put(BarEvent(_sym, _c.conId, _c.localSymbol,
                               pd.Timestamp(b.date, tz="UTC") if not isinstance(b.date, pd.Timestamp) else b.date,
                               float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume or 0)))

            bars.updateEvent += _on_bar_update

        # drain the queue while ib_async's event loop runs in the background
        while True:
            ib.sleep(0.25)  # pumps the ib_async event loop
            while not q.empty():
                yield q.get()


def build_feed(feed_name: str, symbols, **kwargs) -> LiveFeed:
    if feed_name == "databento":
        return DatabentoLiveFeed(symbols)
    if feed_name == "ibkr":
        return IBKRLiveFeed(
            symbols,
            host=kwargs.get("ib_host", os.environ.get("IB_HOST", "127.0.0.1")),
            port=int(kwargs.get("ib_port", os.environ.get("IB_PORT", 7497))),
            client_id=int(kwargs.get("ib_client_id", os.environ.get("IB_CLIENT_ID", 17))),
        )
    raise SystemExit(f"unknown feed '{feed_name}' (choose databento or ibkr)")
