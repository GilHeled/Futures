"""Data-update path — fetch fresh 1m bars, REPAIR the settlement-artifact final bar, append to store.

Run on the host (needs yfinance in the venv). Writes to the Docker store volume via a throwaway
writer container by default, or to a local --store JSONL for dev. The sanitizer (ict_v2.market_data
.sanitize_final_bar) is applied to every symbol's final bar automatically, so the last candle matches
the trading platform's last-traded close instead of yfinance's settlement stamp.

Examples:
  .venv/bin/python -m ict_v2.tools.update_market_data --dry-run
  .venv/bin/python -m ict_v2.tools.update_market_data                 # → docker volume (default)
  .venv/bin/python -m ict_v2.tools.update_market_data --store ict_live_data/raw_1m.jsonl
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ict_v2.market_data import df_to_records, sanitize_final_bar

# yfinance ticker -> store symbol. MNQZ26.CME is the Dec-2026 contract (current front month).
DEFAULT_MAP = {
    "MNQZ26.CME": "CME_MINI:MNQZ2026",
    "MNQ=F": "CME_MINI:MNQ1!",
    "MES=F": "CME_MINI:MES1!",
}
DEFAULT_VOLUME = "trading20_ict_live_data"
DEFAULT_IMAGE = "trading20-v2"
STORE_IN_CONTAINER = "/data/raw_1m.jsonl"


def fetch(ticker: str, period: str):
    import yfinance as yf
    return yf.download(ticker, period=period, interval="1m", progress=False, auto_adjust=False)


def build(mapping: dict, period: str) -> tuple[list[dict], list[dict]]:
    """Fetch + convert + sanitize each symbol. Returns (all_records, repairs)."""
    records: list[dict] = []
    repairs: list[dict] = []
    for ticker, sym in mapping.items():
        recs = df_to_records(fetch(ticker, period), sym)
        if not recs:
            print(f"  {sym:<22} EMPTY (ticker {ticker})")
            continue
        rep = sanitize_final_bar(recs)                    # repair the settle-artifact final bar
        last = recs[-1]
        note = (f"repaired close {rep['old_close']} -> {rep['new_close']} [{rep['direction']}]"
                if rep else "final bar clean")
        print(f"  {sym:<22} {len(recs):>5} bars | last {last['c']} | {note}")
        records.extend(recs)
        if rep:
            repairs.append(rep)
    return records, repairs


def append_local(path: str, records: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    print(f"appended {len(records)} bars -> {path}")


def append_volume(volume: str, image: str, records: list[dict]) -> None:
    """Append to the store inside a Docker named volume via a throwaway writer container."""
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
        for r in records:
            tf.write(json.dumps(r) + "\n")
        tmp = tf.name
    name = "mktdata_writer"
    try:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "run", "-d", "--rm", "-v", f"{volume}:/data",
                        "--entrypoint", "sleep", "--name", name, image, "300"], check=True,
                       capture_output=True)
        subprocess.run(["docker", "cp", tmp, f"{name}:/tmp/upd.jsonl"], check=True, capture_output=True)
        subprocess.run(["docker", "exec", name, "sh", "-c",
                        f"cat /tmp/upd.jsonl >> {STORE_IN_CONTAINER}"], check=True, capture_output=True)
        print(f"appended {len(records)} bars -> volume {volume}:{STORE_IN_CONTAINER}")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        Path(tmp).unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch + sanitize + append 1m market data.")
    ap.add_argument("--period", default="8d", help="yfinance period (1m max ~8d)")
    ap.add_argument("--store", help="append to this local JSONL instead of the Docker volume")
    ap.add_argument("--volume", default=DEFAULT_VOLUME, help="Docker store volume name")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="image for the throwaway writer container")
    ap.add_argument("--dry-run", action="store_true", help="fetch + sanitize + report, do NOT append")
    args = ap.parse_args(argv)

    print(f"fetching 1m (period={args.period}) for {len(DEFAULT_MAP)} symbols:")
    records, repairs = build(DEFAULT_MAP, args.period)
    if not records:
        print("no records fetched; nothing to do")
        return 1
    print(f"total {len(records)} bars | {len(repairs)} final-bar repairs")
    if args.dry_run:
        print("dry-run: not appending")
        return 0
    if args.store:
        append_local(args.store, records)
    else:
        append_volume(args.volume, args.image, records)
    print("done — the chart's /candles reads the store fresh; restart v2 to re-ingest into the engine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
