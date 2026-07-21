"""
Build + save frozen model bundles for the live shadow run (one per
instrument). Run once at go-live; run_shadow --live loads these.

Usage: python -m live_validation.build_bundles --train-end 2026-07-09 --out ./bundles
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import pandas as pd

from live_validation.bundle import DEFAULT_HORIZON, build_bundle
from mnq_system.cli import _resolve_contract_spec
from mnq_system.config import DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import build_provider


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["MYM", "M2K", "MES"])
    ap.add_argument("--train-end", required=True)
    ap.add_argument("--out", default="./bundles")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    args = ap.parse_args()

    provider = build_provider("databento", cache=True)
    train_end = pd.Timestamp(args.train_end, tz="UTC")
    out = Path(args.out)
    for symbol in args.symbols:
        account = replace(DEFAULT_ACCOUNT_CONFIG, contract=_resolve_contract_spec(symbol))
        bars = provider.get_historical_bars(symbol, pd.Timestamp("2019-05-01", tz="UTC").to_pydatetime(),
                                            train_end.to_pydatetime(), "5m")
        bundle = build_bundle(bars, symbol, account.contract.tick_size, account.contract.point_value,
                              account, train_end, horizon=args.horizon)
        path = bundle.save(out / f"{symbol}.joblib")
        print(f"{symbol}: bundle saved -> {path}  (n_train={bundle.meta['n_train_rows']}, "
              f"provenance={bundle.meta['provenance_kind']}:{bundle.meta['commit'][:12]})")


if __name__ == "__main__":
    main()
