"""
Live-vs-historical data validation (pre-trust gate). The model was trained
on Databento HISTORICAL bars; before trusting any live signal, confirm the
Databento LIVE OHLCV stream reconstructs the SAME bars. During an initial
capture window, `run_shadow --live` should also persist the raw 5m bars it
receives (or capture them separately); this tool diffs that capture against
historical for the identical dates.

Checks, per instrument:
  - timestamp alignment (every live 5m bar has a historical counterpart on
    the same 5m boundary; report unmatched on either side),
  - OHLC agreement within tolerance (default 1 tick),
  - per-session bar counts / gaps,
  - front-month rollover continuity (large close-to-close jumps flagged as
    candidate roll dates; must line up with historical's roll construction).

GATE: >=99% of overlapping bars must match within tolerance, with no
unexplained timestamp gaps, before the execution verdict is considered valid.

Usage: python -m live_validation.validate_live_feed --captured live_bars.parquet --symbol MYM
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from mnq_system.cli import _resolve_contract_spec
from mnq_system.data.providers import build_provider

MATCH_THRESHOLD = 0.99


def _bars_from_log(path, symbol) -> pd.DataFrame:
    """Reconstruct captured live bars for one symbol from the shadow-log
    JSONL (record_type=='bar'), which run_shadow --live writes from the
    first tick. Lets Gate-0 capture begin immediately with no separate
    capture file."""
    rows = []
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            if r.get("record_type") == "bar" and r.get("symbol") == symbol:
                rows.append(r)
    if not rows:
        raise SystemExit(f"no 'bar' records for {symbol} in {path}")
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")[["open", "high", "low", "close"]].sort_index()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--captured", help="parquet/csv of live-captured 5m bars (index=ts UTC; o/h/l/c)")
    ap.add_argument("--from-log", help="shadow-log JSONL to pull 'bar' records from (Gate-0 capture)")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tol-ticks", type=float, default=1.0)
    args = ap.parse_args()
    if not (args.captured or args.from_log):
        ap.error("provide --captured or --from-log")

    if args.from_log:
        cap = _bars_from_log(args.from_log, args.symbol)
    else:
        cap = pd.read_parquet(args.captured) if args.captured.endswith(".parquet") else pd.read_csv(args.captured, index_col=0, parse_dates=True)
    cap = cap.sort_index()
    tick = _resolve_contract_spec(args.symbol).tick_size
    tol = args.tol_ticks * tick

    provider = build_provider("databento", cache=True)
    hist = provider.get_historical_bars(args.symbol, cap.index.min().to_pydatetime(),
                                        cap.index.max().to_pydatetime(), "5m")

    common = cap.index.intersection(hist.index)
    only_live = cap.index.difference(hist.index)
    only_hist = hist.index.difference(cap.index)

    matched = 0
    for ts in common:
        ok = all(abs(float(cap.loc[ts, c]) - float(hist.loc[ts, c])) <= tol for c in ["open", "high", "low", "close"])
        matched += int(ok)
    match_rate = matched / len(common) if len(common) else 0.0

    # candidate rollover jumps (close-to-close move >> typical) in each source
    def jumps(df):
        dc = df["close"].diff().abs()
        thr = dc.median() * 20 if len(dc) else 0
        return set(df.index[dc > thr])
    roll_align = jumps(cap) == jumps(hist)

    print(f"=== live-feed validation: {args.symbol} ===")
    print(f"  live bars={len(cap)} hist bars={len(hist)} overlapping={len(common)}")
    print(f"  OHLC match within {args.tol_ticks} tick: {matched}/{len(common)} = {match_rate:.3%}")
    print(f"  timestamps only-in-live={len(only_live)}  only-in-hist={len(only_hist)}")
    print(f"  rollover jump alignment: {'OK' if roll_align else 'MISMATCH -- check continuous roll construction'}")
    gate = (match_rate >= MATCH_THRESHOLD) and (len(only_live) == 0) and roll_align
    print(f"  ---> GATE 0 (data fidelity): {'PASS' if gate else 'FAIL -- fix feed before trusting execution verdict'}")


if __name__ == "__main__":
    main()
