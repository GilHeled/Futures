"""
Local on-disk cache for provider bars.

Paid providers (Databento) bill per request -- re-running the same
historical pull for a follow-up analysis should never cost twice. This
wraps any `DataProvider` and persists each (symbol, interval) pull to a
local Parquet file (inside the project, under `cache/bars/`, NOT the
user's home directory -- easy to find, and obviously part of the project),
satisfying later requests whose range is covered by what's already cached
without touching the underlying provider (or its bill) again.

IMPORTANT caveat this module used to get wrong (fixed): several instruments'
*real* historical data starts later than the date a caller asks for (e.g.
MNQ's own launch date, or a vendor's actual retention window) -- the
provider silently returns less than requested rather than erroring. The
naive "does the cache's earliest bar cover the requested start" check
would then NEVER be satisfied for that exact request, since no amount of
re-fetching produces data before the actual start -- causing a full,
billable re-fetch on every single run using that request, forever. Each
cache file now has a `.meta.json` sidecar recording the *requested* range
of the fetch that produced it; a later request whose [start, end) falls
entirely inside a previously-requested range is treated as covered even if
the underlying data doesn't fully span it, because we already established
(the first time) that this is genuinely all that exists.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from mnq_system.data.providers import DataProvider

# Inside the project (not ~/.cache) so the pulled data is visible and
# obviously part of the project, per explicit user request.
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache" / "bars"

_INTERVAL_TIMEDELTA = {
    "1m": pd.Timedelta(minutes=1),
    "5m": pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
}


class CachingProvider(DataProvider):
    """Wraps another `DataProvider`; caches each (symbol, interval) pull to
    a local Parquet file keyed by `source_name`. A request is served from
    the cache if the cached range already covers [start, end) -- either
    because the cached data itself spans it, or because an earlier fetch
    already requested a range that fully contains this one (see module
    docstring) -- otherwise the *entire* requested range is re-fetched from
    the wrapped provider (not incrementally merged) and the cache file is
    overwritten with it.
    """

    def __init__(self, inner: DataProvider, cache_dir: Path | str = DEFAULT_CACHE_DIR, source_name: str = "default"):
        self._inner = inner
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._source_name = source_name

    def _cache_path(self, symbol: str, interval: str) -> Path:
        safe_symbol = symbol.replace("/", "_").replace("=", "_").replace(".", "_")
        return self._cache_dir / f"{self._source_name}_{safe_symbol}_{interval}.parquet"

    def _meta_path(self, symbol: str, interval: str) -> Path:
        return self._cache_path(symbol, interval).with_suffix(".meta.json")

    def _read_requested_range(self, symbol: str, interval: str) -> Optional[tuple]:
        path = self._meta_path(symbol, interval)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return pd.Timestamp(data["requested_start"]), pd.Timestamp(data["requested_end"])
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def _write_requested_range(self, symbol: str, interval: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> None:
        path = self._meta_path(symbol, interval)
        path.write_text(json.dumps({"requested_start": start_ts.isoformat(), "requested_end": end_ts.isoformat()}))

    def _is_covered(
        self,
        cached: pd.DataFrame,
        start_ts: pd.Timestamp,
        end_ts: pd.Timestamp,
        interval: str,
        requested_range: Optional[tuple],
    ) -> bool:
        if cached.empty:
            return False
        # A previously-requested range that fully contains this request is
        # covered even if the real data doesn't span it -- that's already
        # established fact, not something re-fetching will change.
        if requested_range is not None:
            prev_start, prev_end = requested_range
            if prev_start <= start_ts and prev_end >= end_ts:
                return True
        margin = _INTERVAL_TIMEDELTA.get(interval, pd.Timedelta(0))
        return bool(cached.index.min() <= start_ts and (cached.index.max() + margin) >= end_ts)

    def get_historical_bars(self, symbol: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame:
        start_ts = pd.Timestamp(start)
        start_ts = start_ts.tz_localize("UTC") if start_ts.tz is None else start_ts.tz_convert("UTC")
        end_ts = pd.Timestamp(end)
        end_ts = end_ts.tz_localize("UTC") if end_ts.tz is None else end_ts.tz_convert("UTC")

        path = self._cache_path(symbol, interval)
        requested_range = self._read_requested_range(symbol, interval)
        if path.exists():
            cached = pd.read_parquet(path)
            if self._is_covered(cached, start_ts, end_ts, interval, requested_range):
                return cached.loc[(cached.index >= start_ts) & (cached.index < end_ts)]

        bars = self._inner.get_historical_bars(symbol, start, end, interval)
        bars.to_parquet(path)
        self._write_requested_range(symbol, interval, start_ts, end_ts)
        return bars
