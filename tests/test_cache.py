from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from mnq_system.data.cache import CachingProvider
from mnq_system.data.providers import DataProvider


class _FakeProvider(DataProvider):
    """Records every call so tests can assert whether the wrapped provider
    was actually hit, or whether the cache served the request instead.
    """

    def __init__(self, bars: pd.DataFrame):
        self._bars = bars
        self.calls: list[tuple] = []

    def get_historical_bars(self, symbol, start, end, interval):
        self.calls.append((symbol, start, end, interval))
        start_ts = pd.Timestamp(start).tz_convert("UTC")
        end_ts = pd.Timestamp(end).tz_convert("UTC")
        return self._bars.loc[(self._bars.index >= start_ts) & (self._bars.index < end_ts)]


def _bars(start="2026-01-01", periods=100, freq="5min"):
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": range(periods), "high": range(periods), "low": range(periods), "close": range(periods),
         "volume": [10] * periods},
        index=idx,
    )


class _TruncatedHistoryProvider(DataProvider):
    """Simulates a real vendor (Databento/Massive) whose actual data starts
    later than the caller requests -- silently returns less than asked for,
    the way both real providers do, rather than erroring.
    """

    def __init__(self, bars: pd.DataFrame, real_start: pd.Timestamp):
        self._bars = bars
        self._real_start = real_start
        self.calls: list[tuple] = []

    def get_historical_bars(self, symbol, start, end, interval):
        self.calls.append((symbol, start, end, interval))
        start_ts = max(pd.Timestamp(start).tz_convert("UTC"), self._real_start)
        end_ts = pd.Timestamp(end).tz_convert("UTC")
        return self._bars.loc[(self._bars.index >= start_ts) & (self._bars.index < end_ts)]


def test_caching_provider_fetches_from_inner_on_first_call(tmp_path):
    inner = _FakeProvider(_bars())
    provider = CachingProvider(inner, cache_dir=tmp_path, source_name="test")

    result = provider.get_historical_bars("MNQ", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 5, tzinfo=timezone.utc), "5m")

    assert len(inner.calls) == 1
    assert not result.empty


def test_caching_provider_serves_repeat_request_without_hitting_inner_again(tmp_path):
    inner = _FakeProvider(_bars())
    provider = CachingProvider(inner, cache_dir=tmp_path, source_name="test")
    start, end = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 5, tzinfo=timezone.utc)

    first = provider.get_historical_bars("MNQ", start, end, "5m")
    second = provider.get_historical_bars("MNQ", start, end, "5m")

    assert len(inner.calls) == 1  # only the first call actually fetched
    pd.testing.assert_frame_equal(first, second, check_freq=False)


def test_caching_provider_serves_a_narrower_subrange_from_cache(tmp_path):
    inner = _FakeProvider(_bars())
    provider = CachingProvider(inner, cache_dir=tmp_path, source_name="test")
    wide_start, wide_end = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 5, tzinfo=timezone.utc)
    provider.get_historical_bars("MNQ", wide_start, wide_end, "5m")

    narrow_start = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
    narrow_end = datetime(2026, 1, 1, 2, tzinfo=timezone.utc)
    result = provider.get_historical_bars("MNQ", narrow_start, narrow_end, "5m")

    assert len(inner.calls) == 1  # narrower request served entirely from cache
    assert result.index.min() >= pd.Timestamp(narrow_start)
    assert result.index.max() < pd.Timestamp(narrow_end)


def test_caching_provider_refetches_when_requested_range_extends_beyond_cache(tmp_path):
    inner = _FakeProvider(_bars(periods=200))
    provider = CachingProvider(inner, cache_dir=tmp_path, source_name="test")

    provider.get_historical_bars("MNQ", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 5, tzinfo=timezone.utc), "5m")
    provider.get_historical_bars("MNQ", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 10, tzinfo=timezone.utc), "5m")

    assert len(inner.calls) == 2  # second call's wider range wasn't covered by the first


def test_caching_provider_uses_separate_cache_files_per_interval(tmp_path):
    inner = _FakeProvider(_bars())
    provider = CachingProvider(inner, cache_dir=tmp_path, source_name="test")
    start, end = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 5, tzinfo=timezone.utc)

    provider.get_historical_bars("MNQ", start, end, "5m")
    provider.get_historical_bars("MNQ", start, end, "15m")

    assert len(inner.calls) == 2  # different intervals are different caches
    files = list(tmp_path.glob("*.parquet"))
    assert len(files) == 2


def test_caching_provider_persists_across_instances(tmp_path):
    inner = _FakeProvider(_bars())
    start, end = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, 5, tzinfo=timezone.utc)

    CachingProvider(inner, cache_dir=tmp_path, source_name="test").get_historical_bars("MNQ", start, end, "5m")

    # A fresh CachingProvider instance (e.g. a new script run) should still
    # find the file on disk rather than hitting the inner provider again.
    second_inner = _FakeProvider(_bars())
    CachingProvider(second_inner, cache_dir=tmp_path, source_name="test").get_historical_bars("MNQ", start, end, "5m")

    assert len(second_inner.calls) == 0


def test_caching_provider_does_not_refetch_when_requested_start_predates_real_data(tmp_path):
    # Regression test for a real bug: if the requested start is earlier than
    # the instrument's actual history (e.g. a launch date, or a vendor's
    # real retention window), the naive "cache covers [start, end)" check
    # can never be satisfied -- causing a full, billable re-fetch on every
    # single call using that exact (identical, unchanged) request, forever.
    real_start = pd.Timestamp("2019-05-05", tz="UTC")
    bars = _bars(start="2019-05-05", periods=1000)
    inner = _TruncatedHistoryProvider(bars, real_start)
    provider = CachingProvider(inner, cache_dir=tmp_path, source_name="test")
    requested_start = datetime(2019, 4, 28, tzinfo=timezone.utc)  # earlier than real_start
    requested_end = datetime(2019, 6, 1, tzinfo=timezone.utc)

    provider.get_historical_bars("MNQ", requested_start, requested_end, "5m")
    provider.get_historical_bars("MNQ", requested_start, requested_end, "5m")
    provider.get_historical_bars("MNQ", requested_start, requested_end, "5m")

    assert len(inner.calls) == 1  # only the very first call actually fetched


def test_caching_provider_still_refetches_a_genuinely_wider_request_after_truncated_history(tmp_path):
    # The fix must not become "never re-fetch" -- a request for a range
    # never previously tried should still hit the inner provider once.
    real_start = pd.Timestamp("2019-05-05", tz="UTC")
    bars = _bars(start="2019-05-05", periods=2000)
    inner = _TruncatedHistoryProvider(bars, real_start)
    provider = CachingProvider(inner, cache_dir=tmp_path, source_name="test")

    provider.get_historical_bars(
        "MNQ", datetime(2019, 4, 28, tzinfo=timezone.utc), datetime(2019, 6, 1, tzinfo=timezone.utc), "5m"
    )
    provider.get_historical_bars(
        "MNQ", datetime(2019, 4, 28, tzinfo=timezone.utc), datetime(2019, 12, 1, tzinfo=timezone.utc), "5m"
    )

    assert len(inner.calls) == 2  # second request's wider end date was never previously tried
