"""
Shadow-log runner. Two modes, ONE code path (engine.push -> book.on_bar):

  --replay <parquet-or-cache>: feed cached historical 5m bars through the
    identical streaming engine + shadow book, writing JSONL. No live feed,
    no key. Used for the end-to-end dry-run and (with the parity test) to
    trust the code path before going live. Historical bars carry no quotes,
    so crossing_fill falls back to expected_fill (slippage ~= assumption) --
    a sanity path, not a slippage measurement.

  live: connect to a pluggable LiveFeed (Interactive Brokers by default, or
    Databento) for top-of-book + 5m bars, run forward in real time, and log
    expected vs. crossing fills. The consumer is vendor-agnostic; each vendor
    is one adapter in live_validation.feeds. (Cannot be exercised offline;
    the replay path + parity test are what is verifiable in-repo.)

Usage:
  python -m live_validation.run_shadow --replay --symbols MYM M2K MES \
      --start 2024-01-01 --end 2024-04-01 --train-end 2023-12-31 --out shadow_replay.jsonl
  python -m live_validation.run_shadow --live --feed ibkr --symbols MYM M2K MES \
      --bundles ./bundles --out shadow_live.jsonl        # needs TWS/IB Gateway
  python -m live_validation.run_shadow --live --feed databento --symbols MYM M2K MES \
      --bundles ./bundles --out shadow_live.jsonl        # needs DATABENTO_API_KEY
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from live_validation.bundle import DEFAULT_HORIZON, build_bundle, FrozenBundle
from live_validation.signal_engine import StreamingSignalEngine
from live_validation.shadow_book import ShadowBook
from mnq_system.cli import _resolve_contract_spec
from mnq_system.config import DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import build_provider


class JsonlLogger:
    def __init__(self, path):
        self._f = open(path, "a")

    def write(self, rec: dict):
        self._f.write(json.dumps(rec, default=str) + "\n")
        self._f.flush()

    def close(self):
        self._f.close()


def _account_for(symbol):
    return replace(DEFAULT_ACCOUNT_CONFIG, contract=_resolve_contract_spec(symbol))


def _drive_bar(symbol, engine, book, logger, ts, o, h, low, c, v,
               quote=None, raw_symbol=None, instrument_id=None, quote_valid=True):
    """The one shared step used by both replay and live. `raw_symbol` /
    `instrument_id` identify the exact underlying contract the BAR came
    from; `quote` (if passed) has already been verified to be from the SAME
    contract (see run_live) -- a mismatched quote is dropped upstream, never
    used for a fill. All emitted records carry the contract identifiers for
    full traceability across rollovers."""
    sig = engine.push(ts, {"open": o, "high": h, "low": low, "close": c, "volume": v})
    ohlc = {"open": o, "high": h, "low": low, "close": c, "atr": sig["atr"]}
    tag = {"symbol": symbol, "raw_symbol": raw_symbol, "instrument_id": instrument_id}
    if sig["direction"] != 0 and sig["off_hours"]:
        logger.write({"record_type": "signal", **tag, "ts": str(ts),
                      "direction": sig["direction"], "ev": sig["ev"], "atr": sig["atr"],
                      "bid": (quote or {}).get("bid"), "ask": (quote or {}).get("ask"),
                      "quote_instrument_id": (quote or {}).get("instrument_id")})
    trade = book.on_bar(ts, ohlc, sig["ev"], sig["direction"], sig["off_hours"], sig["session_ending"], quote, quote_valid=quote_valid)
    if trade is not None:
        trade.update(tag)
        trade["quote_instrument_id"] = (quote or {}).get("instrument_id")
        trade["record_type"] = "trade"
        logger.write(trade)


def run_replay(symbols, start, end, train_end, out, horizon=DEFAULT_HORIZON):
    provider = build_provider("databento", cache=True)
    logger = JsonlLogger(out)
    start_ts = pd.Timestamp(start, tz="UTC"); end_ts = pd.Timestamp(end, tz="UTC")
    train_end_ts = pd.Timestamp(train_end, tz="UTC")
    for symbol in symbols:
        account = _account_for(symbol)
        tick, pv = account.contract.tick_size, account.contract.point_value
        # need history up to train_end for the bundle + a warmup lead-in before `start`
        full = provider.get_historical_bars(symbol, pd.Timestamp("2019-05-01", tz="UTC").to_pydatetime(),
                                             end_ts.to_pydatetime(), "5m")
        bundle = build_bundle(full.loc[:train_end_ts], symbol, tick, pv, account, train_end_ts, horizon=horizon)
        engine = StreamingSignalEngine(bundle, account)
        book = ShadowBook(symbol, bundle)
        logger.write({"record_type": "bundle_meta", "symbol": symbol, **bundle.meta})
        # warmup: push lead-in bars so the buffer is primed, then replay [start,end]
        replay_slice = full[(full.index >= start_ts - pd.Timedelta(days=15)) & (full.index <= end_ts)]
        for ts, r in replay_slice.iterrows():
            if ts < start_ts:  # prime buffer only
                engine.push(ts, {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"], "volume": r.get("volume", 0)})
                continue
            _drive_bar(symbol, engine, book, logger, ts, r["open"], r["high"], r["low"], r["close"], r.get("volume", 0))
    logger.close()
    print(f"replay complete -> {out}")


def run_live(symbols, bundles_dir, out, feed_name="ibkr", horizon=DEFAULT_HORIZON, **feed_kwargs):
    """Live shadow run over a pluggable LiveFeed (see live_validation.feeds).
    Loads pre-built frozen bundles (build via build_bundles.py). The consumer
    below is VENDOR-AGNOSTIC -- it only sees normalized Mapping/Quote/Bar
    events -- so switching data vendors is entirely contained in the feed
    adapter and never touches the validated engine/book/logging."""
    from live_validation.feeds import BarEvent, MappingEvent, QuoteEvent, build_feed

    engines, books = {}, {}
    logger = JsonlLogger(out)
    quotes: dict = {}
    for symbol in symbols:
        account = _account_for(symbol)
        bundle = FrozenBundle.load(Path(bundles_dir) / f"{symbol}.joblib")
        engines[symbol] = StreamingSignalEngine(bundle, account)
        books[symbol] = ShadowBook(symbol, bundle)
        logger.write({"record_type": "bundle_meta", "symbol": symbol, **bundle.meta})

    feed = build_feed(feed_name, symbols, **feed_kwargs)
    print(f"live shadow run started ({feed_name}) for {symbols} -> {out}  (Ctrl-C to stop)")
    for ev in feed.stream():
        if isinstance(ev, MappingEvent):
            logger.write({"record_type": "symbol_mapping", "symbol": ev.symbol,
                          "instrument_id": ev.instrument_id, "raw_symbol": ev.raw_symbol,
                          "ts": str(pd.Timestamp.utcnow())})
        elif isinstance(ev, QuoteEvent):
            # tag each quote with the exact contract it came from
            quotes[ev.symbol] = {"bid": ev.bid, "ask": ev.ask,
                                 "instrument_id": ev.instrument_id, "raw_symbol": ev.raw_symbol}
        elif isinstance(ev, BarEvent):
            # Gate-0 capture: persist every live bar from the first tick.
            logger.write({"record_type": "bar", "symbol": ev.symbol, "raw_symbol": ev.raw_symbol,
                          "instrument_id": ev.instrument_id, "ts": str(ev.ts),
                          "open": ev.open, "high": ev.high, "low": ev.low, "close": ev.close, "volume": ev.volume})
            # CONTRACT-CONSISTENCY: use the quote for the fill only if it is
            # from the SAME contract as this bar; a mismatch (typically around
            # rollover) is flagged and the observation marked invalid -- the
            # crossing fill is NOT fabricated (excluded from execution stats).
            q = quotes.get(ev.symbol)
            quote_valid = True
            if q is not None and q.get("instrument_id") != ev.instrument_id:
                logger.write({"record_type": "contract_mismatch", "symbol": ev.symbol, "ts": str(ev.ts),
                              "bar_instrument_id": ev.instrument_id, "bar_raw_symbol": ev.raw_symbol,
                              "quote_instrument_id": q.get("instrument_id"), "quote_raw_symbol": q.get("raw_symbol")})
                q = None
                quote_valid = False
            _drive_bar(ev.symbol, engines[ev.symbol], books[ev.symbol], logger, ev.ts,
                       ev.open, ev.high, ev.low, ev.close, ev.volume,
                       quote=q, raw_symbol=ev.raw_symbol, instrument_id=ev.instrument_id, quote_valid=quote_valid)
    logger.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--symbols", nargs="+", default=["MYM", "M2K", "MES"])
    ap.add_argument("--start"); ap.add_argument("--end"); ap.add_argument("--train-end")
    ap.add_argument("--bundles", default="./bundles")
    ap.add_argument("--out", default="shadow_log.jsonl")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    ap.add_argument("--feed", choices=["ibkr", "databento"], default="ibkr",
                    help="live data source (default: ibkr)")
    ap.add_argument("--ib-host", default=None, help="IB Gateway/TWS host (Docker: host.docker.internal)")
    ap.add_argument("--ib-port", type=int, default=None, help="IB API port (7497 paper TWS / 4002 paper Gateway)")
    ap.add_argument("--ib-client-id", type=int, default=None)
    args = ap.parse_args()
    if args.replay:
        run_replay(args.symbols, args.start, args.end, args.train_end, args.out, args.horizon)
    elif args.live:
        feed_kwargs = {}
        if args.ib_host is not None:
            feed_kwargs["ib_host"] = args.ib_host
        if args.ib_port is not None:
            feed_kwargs["ib_port"] = args.ib_port
        if args.ib_client_id is not None:
            feed_kwargs["ib_client_id"] = args.ib_client_id
        run_live(args.symbols, args.bundles, args.out, feed_name=args.feed, horizon=args.horizon, **feed_kwargs)
    else:
        ap.error("choose --replay or --live")


if __name__ == "__main__":
    main()
