"""
Data provider abstraction for historical (backtest) and near-real-time (live,
signal-only) OHLCV futures bars.

Why this abstraction exists
----------------------------
"Best API" depends on budget and how much history you need, so the system is
built against an interface (`DataProvider`) rather than one vendor:

- `YFinanceProvider` (free): uses Yahoo Finance's continuous front-month
  future symbol (e.g. "MNQ=F"). Zero cost, good for a quick smoke-test of the
  pipeline, but Yahoo does NOT retain deep intraday history -- as of this
  writing it serves roughly the last ~60 calendar days for 5m/15m/30m/60m
  bars and roughly the last ~7-8 days for 1m bars, and that window is a hard
  data-retention limit, not a request quota (chunking requests will not
  recover older intraday bars). That is well short of the 6-12 months of
  multi-regime data `references/verification-methodology.md` recommends
  before trusting a parameter set. Treat yfinance as a pipeline smoke-test /
  live-quote-adjacent source, not the basis for a real verification backtest.
- `DatabentoProvider` (paid, pay-as-you-go, $125 free credit as of this
  writing): pulls directly from CME Globex MDP 3.0 (dataset "GLBX.MDP3"),
  the official exchange feed for MNQ. Requires a `databento` account + API
  key (`DATABENTO_API_KEY` env var) and the `databento` pip package. Signup
  asks for a card even though the free credit likely covers an OHLCV pull.
- `MassiveProvider` (free, no credit card, formerly Polygon.io): confirmed
  working against a real free-tier key as of this writing -- serves real
  CME futures OHLCV aggregates with no paywall on the aggregates or
  contracts-reference endpoints. Two tradeoffs versus Databento, both
  confirmed empirically (not just from docs):
  1. **Depth is much shallower than advertised for MNQ specifically**: the
     contracts *reference* endpoint lists contracts going back to 2019
     (MNQ's launch), but the *aggregates* endpoint returns zero bars for
     any of them before 2024-07-09 -- i.e. as of this writing there is
     only ~2 years of real MNQ intraday history here, not "10+ years."
     `get_historical_bars` raises a `RuntimeWarning` if this happens so it
     is never silently swallowed into a shorter-than-requested backtest.
     Confirm current depth before assuming it's grown.
  2. No continuous-contract symbol, so this provider rolls across each
     quarterly contract's own active window and concatenates them with NO
     back-adjustment for the price gap at the roll date -- expect small,
     visible discontinuities in the series right at contract expiries.
  Get a key at massive.com (dashboard -> API keys) and set `MASSIVE_API_KEY`.
- `CsvProvider`: loads bars from a local CSV (e.g. exported from a vendor,
  a broker, or TradingView's manual per-symbol CSV export). Also what the
  test suite uses to stay network-free.

Note on TradingView: TradingView does not offer an official public API for
bulk historical OHLCV export suitable for programmatic backtesting -- its
data license covers the charting UI, and scripted scraping of it would
violate its Terms of Service. If you have a TradingView account, the
supported path is to use its manual "Export chart data" CSV download and
feed it through `CsvProvider`, not to script against it directly.

Confirm current pricing/coverage/ToS directly with each vendor before
relying on this summary -- it reflects general documentation at the time
this system was built, not a live-verified quote.
"""

from __future__ import annotations

import abc
import os
import re
import time
import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd

from mnq_system.data.resample import resample_ohlcv

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a data request."""


class DataProvider(abc.ABC):
    """Interface every historical/live data source must implement."""

    @abc.abstractmethod
    def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: str
    ) -> pd.DataFrame:
        """Return OHLCV bars in [start, end) at the given interval ("1m","5m","15m",...).

        Must return a DataFrame indexed by a tz-aware UTC DatetimeIndex with
        columns open/high/low/close/volume, sorted ascending, with no
        duplicate timestamps. Bar timestamps label the bar's OPEN time.
        """
        raise NotImplementedError

    def get_latest_bars(self, symbol: str, interval: str, lookback_bars: int) -> pd.DataFrame:
        """Default 'live' polling implementation: fetch a short recent window.

        Providers with a true streaming API may override this for lower
        latency; the default just re-uses get_historical_bars with a window
        sized to comfortably cover `lookback_bars` at `interval`.
        """
        minutes = _interval_to_minutes(interval)
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes * (lookback_bars + 5))
        return self.get_historical_bars(symbol, start, end, interval)


def _interval_to_minutes(interval: str) -> int:
    return {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}[interval]


def _validate_bars(bars: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        raise DataProviderError(f"provider returned bars missing columns: {missing}")
    bars = bars[REQUIRED_COLUMNS].copy()
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    return bars


class YFinanceProvider(DataProvider):
    """Free provider backed by Yahoo Finance continuous futures symbols.

    Suitable for a quick end-to-end pipeline smoke-test. NOT suitable as the
    sole basis for the verification backtest described in
    `references/verification-methodology.md` because of Yahoo's short
    intraday retention window (see module docstring).
    """

    # Yahoo's approximate hard intraday retention windows, in days, as of
    # this writing. Used only to give an early, clear error instead of a
    # silent empty result when a request falls outside them.
    _RETENTION_DAYS = {"1m": 8, "5m": 60, "15m": 60, "30m": 60, "1h": 730}

    def __init__(self, yf_module=None):
        if yf_module is None:
            import yfinance as yf  # imported lazily so it's an optional dep

            yf_module = yf
        self._yf = yf_module

    def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: str
    ) -> pd.DataFrame:
        retention = self._RETENTION_DAYS.get(interval)
        if retention is not None:
            oldest_available = datetime.now(timezone.utc) - timedelta(days=retention)
            if start < oldest_available:
                raise DataProviderError(
                    f"yfinance only retains ~{retention} days of '{interval}' bars; "
                    f"requested start {start} is before {oldest_available}. "
                    "Use DatabentoProvider (or another vendor) for deeper history."
                )

        raw = self._yf.download(
            tickers=symbol,
            start=start,
            end=end,
            interval=interval,
            auto_adjust=False,
            progress=False,
        )
        if raw is None or raw.empty:
            raise DataProviderError(
                f"yfinance returned no data for {symbol} [{start} - {end}] @ {interval}"
            )

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.rename(columns=str.lower)

        if raw.index.tz is None:
            raw.index = raw.index.tz_localize("America/New_York").tz_convert("UTC")
        else:
            raw.index = raw.index.tz_convert("UTC")

        return _validate_bars(raw)


class DatabentoProvider(DataProvider):
    """Paid provider backed by CME Globex MDP 3.0 via Databento (recommended
    for real backtest verification -- official exchange-sourced data).

    Always fetches native 1-minute OHLCV and resamples locally to the
    requested interval, so 5m/15m bars are built consistently with every
    other provider in this system.
    """

    def __init__(self, api_key: str | None = None, client=None, dataset: str = "GLBX.MDP3"):
        self._dataset = dataset
        self._api_key = api_key
        # Client construction (and the API-key check) is deferred to first
        # actual fetch rather than done here -- so a request fully served by
        # CachingProvider from disk never needs a real key, and can never
        # accidentally touch (or bill) the network.
        self._client = client

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = self._api_key or os.environ.get("DATABENTO_API_KEY")
        if not api_key:
            raise DataProviderError(
                "Databento API key not found. Set DATABENTO_API_KEY or pass api_key=."
            )
        import databento as db  # imported lazily so it's an optional dep

        self._client = db.Historical(api_key)
        return self._client

    def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: str
    ) -> pd.DataFrame:
        continuous_symbol = symbol if ".c." in symbol else f"{symbol}.c.0"
        try:
            data = self._get_client().timeseries.get_range(
                dataset=self._dataset,
                schema="ohlcv-1m",
                stype_in="continuous",
                symbols=[continuous_symbol],
                start=start,
                end=end,
            )
            raw = data.to_df()
        except Exception as exc:  # pragma: no cover - network/SDK error path
            raise DataProviderError(f"Databento request failed: {exc}") from exc

        if raw is None or raw.empty:
            raise DataProviderError(
                f"Databento returned no data for {continuous_symbol} [{start} - {end}]"
            )

        raw = raw.rename(columns=str.lower)
        if raw.index.tz is None:
            raw.index = raw.index.tz_localize("UTC")
        else:
            raw.index = raw.index.tz_convert("UTC")

        one_min = _validate_bars(raw)
        if interval == "1m":
            return one_min
        return resample_ohlcv(one_min, interval)


class MassiveProvider(DataProvider):
    """Free, no-credit-card CME futures data via Massive (formerly Polygon.io).

    Uses `product_code` (e.g. "MNQ") rather than a single ticker, since
    Massive has no continuous-contract symbol -- each dated quarterly
    contract (e.g. "MNQU6") only trades for a few months around its own
    expiry. This provider looks up the contract sequence via the
    `/futures/v1/contracts` reference endpoint and rolls across each
    contract's own active window, concatenating them with NO price
    back-adjustment at the roll -- expect a small visible discontinuity in
    the series right at each contract expiry. Get a key at massive.com
    (dashboard -> API keys, no card required for the free tier) and set
    `MASSIVE_API_KEY`.

    Confirmed empirically: real MNQ aggregate history only goes back to
    2024-07-09 as of this writing, well short of the "10+ years" the
    product markets -- the reference endpoint happily lists older expired
    contracts, but their bar data doesn't actually exist. A request that
    starts earlier than the real data emits a `RuntimeWarning` naming the
    actual start rather than silently returning a shorter series.
    """

    BASE_URL = "https://api.polygon.io"
    _RESOLUTION_MAP = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1hour"}
    # Matches a single dated contract ticker (e.g. "MNQU6") and excludes
    # calendar-spread tickers the reference endpoint also returns (e.g.
    # "MNQH0-MNQM1").
    _TICKER_RE = re.compile(r"^[A-Z]{1,4}[FGHJKMNQUVXZ]\d$")

    _MAX_RATE_LIMIT_RETRIES = 5

    def __init__(
        self,
        api_key: str | None = None,
        session=None,
        min_request_interval_sec: float = 13.0,
    ):
        api_key = api_key or os.environ.get("MASSIVE_API_KEY") or os.environ.get("POLYGON_API_KEY")
        if not api_key:
            raise DataProviderError(
                "Massive API key not found. Set MASSIVE_API_KEY (or POLYGON_API_KEY) or pass api_key=."
            )
        self._api_key = api_key
        if session is None:
            import requests  # imported lazily so it's an optional dep

            session = requests.Session()
        self._session = session
        # Free tier is capped at 5 requests/minute; the default spacing here
        # keeps comfortably under that rather than relying on retries alone.
        # Tests inject 0 so the mocked-session suite stays fast.
        self._min_request_interval_sec = min_request_interval_sec
        self._last_request_monotonic = 0.0

    def _throttle(self) -> None:
        if self._min_request_interval_sec <= 0:
            return
        elapsed = time.monotonic() - self._last_request_monotonic
        remaining = self._min_request_interval_sec - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, url: str, params: dict | None = None) -> dict:
        for attempt in range(self._MAX_RATE_LIMIT_RETRIES):
            self._throttle()
            resp = self._session.get(url, params=params, headers={"Authorization": f"Bearer {self._api_key}"}, timeout=30)
            self._last_request_monotonic = time.monotonic()
            if resp.status_code == 429:
                if attempt == self._MAX_RATE_LIMIT_RETRIES - 1:
                    raise DataProviderError("Massive API rate limit (429) persisted after retries -- try again shortly")
                if self._min_request_interval_sec > 0:
                    time.sleep(self._min_request_interval_sec)
                continue
            if resp.status_code != 200:
                raise DataProviderError(f"Massive API request failed ({resp.status_code}): {resp.text[:300]}")
            return resp.json()
        raise DataProviderError("Massive API rate limit (429) persisted after retries -- try again shortly")

    def _list_contracts(
        self, product_code: str, date_gte: pd.Timestamp | None = None, date_lte: pd.Timestamp | None = None
    ) -> list[tuple[str, str]]:
        """Reference-endpoint lookup, filtered to a narrow `date` window when
        given. The endpoint has years of contracts on file and paginates
        oldest-first, so an unfiltered call never reaches recent contracts
        within a sane page limit -- always pass a window when looking for
        "the contract active around this point in time".
        """
        params: dict = {"product_code": product_code, "limit": 250}
        if date_gte is not None:
            params["date.gte"] = date_gte.strftime("%Y-%m-%d")
        if date_lte is not None:
            params["date.lte"] = date_lte.strftime("%Y-%m-%d")
        data = self._get(f"{self.BASE_URL}/futures/v1/contracts", params=params)
        tickers: dict[str, str] = {}
        for row in data.get("results", []):
            ticker = row.get("ticker", "")
            last_trade_date = row.get("last_trade_date")
            if last_trade_date and self._TICKER_RE.match(ticker):
                tickers[ticker] = last_trade_date
        return sorted(tickers.items(), key=lambda kv: kv[1])

    def _find_contract_covering(self, product_code: str, as_of: pd.Timestamp) -> tuple[str, pd.Timestamp] | None:
        """The contract whose active window covers `as_of`: found by looking
        up whatever was listed in a narrow window starting at `as_of`, then
        taking the one expiring soonest (that hasn't already expired).
        """
        candidates = self._list_contracts(product_code, date_gte=as_of, date_lte=as_of + pd.Timedelta(days=10))
        valid = [(ticker, pd.Timestamp(last_trade, tz="UTC")) for ticker, last_trade in candidates]
        valid = [(ticker, last_trade) for ticker, last_trade in valid if last_trade >= as_of]
        if not valid:
            return None
        return min(valid, key=lambda kv: kv[1])

    def _fetch_ticker_bars(self, ticker: str, start: pd.Timestamp, end: pd.Timestamp, resolution: str) -> list[dict]:
        url = f"{self.BASE_URL}/futures/v1/aggs/{ticker}"
        params = {
            "resolution": resolution,
            "window_start.gte": start.strftime("%Y-%m-%d"),
            "window_start.lt": end.strftime("%Y-%m-%d"),
            "limit": 50000,
        }
        rows: list[dict] = []
        next_url = None
        while True:
            data = self._get(next_url or url, params=None if next_url else params)
            rows.extend(data.get("results", []))
            next_url = data.get("next_url")
            if not next_url:
                break
        return rows

    def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: str
    ) -> pd.DataFrame:
        resolution = self._RESOLUTION_MAP.get(interval)
        if resolution is None:
            raise DataProviderError(f"Massive provider does not support interval '{interval}'")

        start_ts = pd.Timestamp(start)
        start_ts = start_ts.tz_localize("UTC") if start_ts.tz is None else start_ts.tz_convert("UTC")
        end_ts = pd.Timestamp(end)
        end_ts = end_ts.tz_localize("UTC") if end_ts.tz is None else end_ts.tz_convert("UTC")

        rows: list[dict] = []
        window_lo = start_ts
        seen_tickers: set[str] = set()
        while window_lo < end_ts:
            found = self._find_contract_covering(symbol, window_lo)
            if found is None:
                break
            ticker, last_trade_ts = found
            if ticker in seen_tickers:
                break  # safety net against a malformed roll producing no progress
            seen_tickers.add(ticker)
            window_hi = min(end_ts, last_trade_ts + pd.Timedelta(days=1))
            rows.extend(self._fetch_ticker_bars(ticker, window_lo, window_hi, resolution))
            window_lo = window_hi

        if not rows:
            raise DataProviderError(f"Massive returned no bars for {symbol} [{start} - {end}] @ {interval}")

        raw = pd.DataFrame(rows)
        raw["timestamp"] = pd.to_datetime(raw["window_start"], unit="ns", utc=True)
        raw = raw.set_index("timestamp")
        result = _validate_bars(raw)

        # The contracts *reference* endpoint lists contracts going back years,
        # but the *aggregates* endpoint has been observed to have a hard floor
        # on how far back real bar data actually exists (for MNQ specifically,
        # nothing before 2024-07-09 as of this writing) -- silently returning
        # a narrower dataset than requested is exactly the kind of thing that
        # inflates a "4-year backtest" into an unnoticed 2-year one.
        earliest = result.index.min()
        if earliest > start_ts + pd.Timedelta(days=2):
            warnings.warn(
                f"Massive has no {symbol} bars before {earliest.date()} even though "
                f"start={start_ts.date()} was requested -- the reference endpoint lists "
                f"older contracts, but their aggregate bar data isn't actually available. "
                f"Effective backtest window starts at {earliest.date()}, not {start_ts.date()}.",
                RuntimeWarning,
                stacklevel=2,
            )

        return result


class CsvProvider(DataProvider):
    """Loads bars from a local CSV: columns timestamp,open,high,low,close,volume.

    `timestamp` must be ISO-8601 and either tz-aware or assumed UTC if naive.
    Native resolution is inferred from the median bar spacing; requests for
    a coarser interval are served via local resampling.
    """

    def __init__(self, path: str):
        df = pd.read_csv(path, parse_dates=["timestamp"])
        df = df.set_index("timestamp")
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        self._bars = _validate_bars(df)
        spacing = self._bars.index.to_series().diff().median()
        self._native_minutes = max(1, round(spacing.total_seconds() / 60)) if pd.notna(spacing) else 1

    def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: str
    ) -> pd.DataFrame:
        start = pd.Timestamp(start).tz_localize("UTC") if pd.Timestamp(start).tz is None else pd.Timestamp(start)
        end = pd.Timestamp(end).tz_localize("UTC") if pd.Timestamp(end).tz is None else pd.Timestamp(end)
        window = self._bars.loc[(self._bars.index >= start) & (self._bars.index < end)]
        if window.empty:
            raise DataProviderError(f"CSV has no bars in [{start} - {end})")
        target_minutes = _interval_to_minutes(interval)
        if target_minutes == self._native_minutes:
            return window
        if target_minutes < self._native_minutes:
            raise DataProviderError(
                f"CSV native resolution is {self._native_minutes}m; cannot derive finer '{interval}' bars"
            )
        return resample_ohlcv(window, interval)


def build_provider(name: str, cache: bool = False, **kwargs) -> DataProvider:
    """Factory used by the CLI: name in {"yfinance", "databento", "massive", "csv"}.

    `cache=True` wraps the result in `CachingProvider` (see
    mnq_system/data/cache.py) so a repeated pull of the same range from a
    paid (Databento) or rate-limited (Massive) provider is served locally
    instead of re-fetched (and, for Databento, re-billed).
    """
    name = name.lower()
    if name == "yfinance":
        provider: DataProvider = YFinanceProvider()
    elif name == "databento":
        provider = DatabentoProvider(api_key=kwargs.get("api_key"))
    elif name == "massive":
        provider = MassiveProvider(api_key=kwargs.get("api_key"))
    elif name == "csv":
        path = kwargs.get("path")
        if not path:
            raise DataProviderError("csv provider requires path=")
        provider = CsvProvider(path)
    else:
        raise DataProviderError(f"Unknown data provider '{name}'")

    if cache and name != "csv":  # a CSV file is already local; no point caching it again
        from mnq_system.data.cache import CachingProvider  # local import avoids a module-level cycle

        provider = CachingProvider(provider, source_name=name)
    return provider
