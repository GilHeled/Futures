"""Daily futures data for the trend+carry substrate.

We pull the **parent** symbology (all dated outright contracts) per root at daily
resolution and build the volume-roll continuous + carry term structure LOCALLY.

Why not Databento's continuous `.v.0`/`.v.1` directly (the frozen choice)?
Databento's continuous `ohlcv-1d` streaming is unusably slow (~24s per *year*
for one symbol; the full-range multi-symbol request hangs indefinitely). Parent
symbology returns every dated contract for a root in ~9s over the full 16 years.
Building the volume roll ourselves from the same underlying contracts is faithful
to the frozen decision ("volume roll, front + next rank") — it is an
implementation route to the same rule, and it is more auditable. Documented as an
implementation-blocker fix per the frozen protocol; the design is unchanged.

Contracts are keyed by `instrument_id` (unique), NOT the raw symbol string, which
repeats across decades (e.g. `ESH3` = 2013 and 2023 are different instruments).
Calendar spreads (symbol contains '-') are excluded.
"""
from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime

import pandas as pd

from trend_carry import config as C

_PROJECT = pathlib.Path(__file__).resolve().parents[1]
CACHE_DIR = _PROJECT / "cache" / "bars"


def _parquet(root: str) -> pathlib.Path:
    return CACHE_DIR / f"tc_{root}_parent_1d.parquet"


def _meta(root: str) -> pathlib.Path:
    return CACHE_DIR / f"tc_{root}_parent_1d.meta.json"


# --------------------------------------------------------------------------- #
# Key + client                                                                #
# --------------------------------------------------------------------------- #
def _load_key() -> str:
    import os

    k = os.environ.get("DATABENTO_API_KEY")
    if k:
        return k
    raw = (_PROJECT / ".claude" / "settings.local.json").read_text()
    m = re.search(r"db-[A-Za-z0-9]{20,}", raw)
    if not m:
        raise RuntimeError("DATABENTO_API_KEY not found in env or settings.local.json")
    return m.group(0)


def _client():
    import databento as db

    return db.Historical(_load_key())


# --------------------------------------------------------------------------- #
# Cost estimate (free metadata)                                               #
# --------------------------------------------------------------------------- #
def estimate_cost(client=None) -> float:
    client = client or _client()
    syms = [f"{r}.FUT" for r in C.ROOTS]
    return float(client.metadata.get_cost(
        dataset=C.DATASET, symbols=syms, schema=C.SCHEMA_DAILY,
        stype_in="parent", start=C.DATA_START, end=C.DATA_END))


# --------------------------------------------------------------------------- #
# Per-root pull + cache                                                       #
# --------------------------------------------------------------------------- #
def _covers(root: str) -> bool:
    if not (_parquet(root).exists() and _meta(root).exists()):
        return False
    m = json.loads(_meta(root).read_text())
    return m.get("start", "9999") <= C.DATA_START and m.get("end", "0000") >= C.DATA_END


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.lower)
    if "ts_event" in df.columns:
        df = df.set_index("ts_event")
    df.index = pd.to_datetime(df.index, utc=True).normalize()
    df = df[["symbol", "instrument_id", "open", "high", "low", "close", "volume"]].copy()
    # outrights only (exclude calendar spreads like ESH3-ESM3)
    df = df[~df["symbol"].str.contains("-", regex=False)]
    df = df[df["close"] > 0]
    return df.sort_index()


def pull_root(root: str, client=None, force: bool = False) -> pd.DataFrame:
    if _covers(root) and not force:
        return load_parent(root)
    client = client or _client()
    data = client.timeseries.get_range(
        dataset=C.DATASET, schema=C.SCHEMA_DAILY, stype_in="parent",
        symbols=[f"{root}.FUT"], start=C.DATA_START, end=C.DATA_END)
    df = _normalize(data.to_df())
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_parquet(root))
    _meta(root).write_text(json.dumps({
        "root": root, "start": C.DATA_START, "end": C.DATA_END, "schema": C.SCHEMA_DAILY,
        "dataset": C.DATASET, "stype_in": "parent", "rows": int(len(df)),
        "instrument_ids": int(df["instrument_id"].nunique()),
        "pulled_at": datetime.utcnow().isoformat() + "Z"}, indent=2))
    print(f"  {root}: {len(df)} outright rows, {df['instrument_id'].nunique()} contracts", flush=True)
    return df


def pull_universe(force: bool = False) -> None:
    need = [r for r in C.ROOTS if force or not _covers(r)]
    if not need:
        print("[trend_carry.data] all roots cached; nothing to pull.")
        return
    client = _client()
    cost = estimate_cost(client)
    print(f"[trend_carry.data] get_cost for {len(C.ROOTS)} parent roots "
          f"{C.DATA_START}..{C.DATA_END}: ${cost:.4f}", flush=True)
    if cost > 15.0:
        raise RuntimeError(f"Cost ${cost:.2f} exceeds $15 guard; aborting.")
    for r in need:
        pull_root(r, client=client, force=force)
    print("[trend_carry.data] pull complete.", flush=True)


def load_parent(root: str) -> pd.DataFrame:
    p = _parquet(root)
    if not p.exists():
        raise FileNotFoundError(f"{p} missing; run pull_universe() first.")
    return pd.read_parquet(p)


# --------------------------------------------------------------------------- #
# Cache-safety verification (cost discipline)                                 #
# --------------------------------------------------------------------------- #
class _ExplodingClient:
    def __getattr__(self, _):
        raise AssertionError("network client touched during a cached load!")


def verify_cache_no_refetch() -> bool:
    for r in C.ROOTS:
        assert _covers(r), f"cache does not cover {r}"
        pull_root(r, client=_ExplodingClient(), force=False)  # must not call client
    return True


if __name__ == "__main__":
    pull_universe()
    verify_cache_no_refetch()
    print("cache no-refetch verification: PASS")
