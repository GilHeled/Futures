"""
Shadow-log runner. Two modes, ONE code path (engine.push -> book.on_bar):

  --replay <parquet-or-cache>: feed cached historical 5m bars through the
    identical streaming engine + shadow book, writing JSONL. No live feed,
    no key. Used for the end-to-end dry-run and (with the parity test) to
    trust the code path before going live. Historical bars carry no quotes,
    so crossing_fill falls back to expected_fill (slippage ~= assumption) --
    a sanity path, not a slippage measurement.

  live: connect to Databento live (top-of-book + 5m bars) for MYM/M2K/MES,
    run forward in real time, log expected vs. crossing fills. Requires a
    Databento LIVE entitlement and DATABENTO_API_KEY in the environment.
    (Cannot be exercised offline; the replay path + parity test are what is
    verifiable in-repo.)

Usage:
  python -m live_validation.run_shadow --replay --symbols MYM M2K MES \
      --start 2024-01-01 --end 2024-04-01 --train-end 2023-12-31 --out shadow_replay.jsonl
  python -m live_validation.run_shadow --live --symbols MYM M2K MES \
      --bundles ./bundles --out shadow_live.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path

import pandas as pd

from live_validation.bundle import DEFAULT_HORIZON, build_bundle, FrozenBundle
from live_validation.inference import ATR_PERIOD
from live_validation.signal_engine import StreamingSignalEngine
from live_validation.shadow_book import ShadowBook
from mnq_system.cli import _resolve_contract_spec
from mnq_system.config import DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import build_provider
from mnq_system.indicators import atr

CONTINUOUS = {"MYM": "MYM.c.0", "M2K": "M2K.c.0", "MES": "MES.c.0"}  # Databento continuous front-month


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


def run_live(symbols, bundles_dir, out, horizon=DEFAULT_HORIZON):
    """Databento live shadow run. Requires DATABENTO_API_KEY + a live
    entitlement. Loads pre-built frozen bundles (build via build_bundles.py)."""
    import databento as db  # local import: only needed for live

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise SystemExit("DATABENTO_API_KEY not set in environment.")

    engines, books = {}, {}
    logger = JsonlLogger(out)
    quotes: dict = {}
    for symbol in symbols:
        account = _account_for(symbol)
        bundle = FrozenBundle.load(Path(bundles_dir) / f"{symbol}.joblib")
        engines[symbol] = StreamingSignalEngine(bundle, account)
        books[symbol] = ShadowBook(symbol, bundle)
        logger.write({"record_type": "bundle_meta", "symbol": symbol, **bundle.meta})

    cont_to_sym = {CONTINUOUS[s]: s for s in symbols}
    # instrument_id -> (my_symbol, raw_contract). Maintained from Databento
    # SymbolMappingMsgs so every bar/quote is tied to the EXACT underlying
    # contract, and rollovers (instrument_id changes) are tracked explicitly.
    id_to_symbol: dict = {}
    id_to_raw: dict = {}
    scale = 1e9

    client = db.Live(key=key)
    client.subscribe(dataset="GLBX.MDP3", schema="mbp-1", stype_in="continuous", symbols=[CONTINUOUS[s] for s in symbols])
    client.subscribe(dataset="GLBX.MDP3", schema="ohlcv-5m", stype_in="continuous", symbols=[CONTINUOUS[s] for s in symbols])
    print(f"live shadow run started for {symbols} -> {out}  (Ctrl-C to stop)")
    for record in client:
        # symbol-mapping: resolve continuous -> instrument_id -> raw contract
        if hasattr(record, "stype_out_symbol") and hasattr(record, "instrument_id"):
            iid = record.instrument_id
            cont = getattr(record, "stype_in_symbol", None)
            sym = cont_to_sym.get(cont)
            if sym is not None:
                id_to_symbol[iid] = sym
                id_to_raw[iid] = getattr(record, "stype_out_symbol", None)
                logger.write({"record_type": "symbol_mapping", "symbol": sym,
                              "instrument_id": iid, "raw_symbol": id_to_raw[iid], "ts": str(pd.Timestamp.utcnow())})
            continue

        iid = getattr(record, "instrument_id", None)
        symbol = id_to_symbol.get(iid)
        if symbol is None:
            continue  # not yet mapped / not one of ours
        raw_symbol = id_to_raw.get(iid)

        # top-of-book update -- tag the quote with its own contract id
        if hasattr(record, "levels") and record.levels:
            lvl = record.levels[0]
            quotes[symbol] = {"bid": lvl.bid_px / scale, "ask": lvl.ask_px / scale,
                              "instrument_id": iid, "raw_symbol": raw_symbol}
        # completed 5m bar
        elif all(hasattr(record, f) for f in ("open", "high", "low", "close")):
            ts = pd.Timestamp(record.ts_event, unit="ns", tz="UTC")
            # Gate-0 capture: persist every live bar from the first tick, with
            # its resolved contract identity, regardless of whether it trades.
            logger.write({"record_type": "bar", "symbol": symbol, "raw_symbol": raw_symbol,
                          "instrument_id": iid, "ts": str(ts),
                          "open": record.open / scale, "high": record.high / scale,
                          "low": record.low / scale, "close": record.close / scale,
                          "volume": getattr(record, "volume", 0)})
            # CONTRACT-CONSISTENCY: only use the quote for the fill if it is
            # from the SAME contract as this bar; a mismatch (typically around
            # rollover) would masquerade as slippage, so drop it and flag it.
            q = quotes.get(symbol)
            quote_valid = True
            if q is not None and q.get("instrument_id") != iid:
                logger.write({"record_type": "contract_mismatch", "symbol": symbol, "ts": str(ts),
                              "bar_instrument_id": iid, "bar_raw_symbol": raw_symbol,
                              "quote_instrument_id": q.get("instrument_id"), "quote_raw_symbol": q.get("raw_symbol")})
                # contract mismatch -> the crossing observation is unreliable;
                # mark it invalid (excluded from execution stats), do NOT
                # fabricate an expected-fill crossing.
                q = None
                quote_valid = False
            _drive_bar(symbol, engines[symbol], books[symbol], logger, ts,
                       record.open / scale, record.high / scale, record.low / scale, record.close / scale,
                       getattr(record, "volume", 0), quote=q, raw_symbol=raw_symbol, instrument_id=iid,
                       quote_valid=quote_valid)
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
    args = ap.parse_args()
    if args.replay:
        run_replay(args.symbols, args.start, args.end, args.train_end, args.out, args.horizon)
    elif args.live:
        run_live(args.symbols, args.bundles, args.out, args.horizon)
    else:
        ap.error("choose --replay or --live")


if __name__ == "__main__":
    main()
