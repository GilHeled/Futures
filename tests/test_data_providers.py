import warnings
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from mnq_system.data.providers import (
    CsvProvider,
    DatabentoProvider,
    DataProviderError,
    MassiveProvider,
    YFinanceProvider,
    build_provider,
)


def _write_csv(tmp_path, freq="5min", n=20):
    idx = pd.date_range("2026-01-01 09:30", periods=n, freq=freq, tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": idx,
            "open": [100.0 + i for i in range(n)],
            "high": [100.5 + i for i in range(n)],
            "low": [99.5 + i for i in range(n)],
            "close": [100.2 + i for i in range(n)],
            "volume": [1000] * n,
        }
    )
    path = tmp_path / "bars.csv"
    df.to_csv(path, index=False)
    return path, idx


def test_csv_provider_returns_bars_within_requested_window(tmp_path):
    # Arrange
    path, idx = _write_csv(tmp_path)
    provider = CsvProvider(str(path))

    # Act
    result = provider.get_historical_bars("MNQ", idx[5].to_pydatetime(), idx[10].to_pydatetime(), "5m")

    # Assert
    assert len(result) == 5
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]


def test_csv_provider_raises_when_window_has_no_bars(tmp_path):
    path, idx = _write_csv(tmp_path)
    provider = CsvProvider(str(path))

    with pytest.raises(DataProviderError):
        provider.get_historical_bars("MNQ", datetime(2030, 1, 1), datetime(2030, 1, 2), "5m")


def test_csv_provider_resamples_up_to_a_coarser_interval(tmp_path):
    # Arrange: 20 bars of native 5m data -> requesting 15m should yield ~1/3 as many bars
    path, idx = _write_csv(tmp_path, freq="5min", n=30)
    provider = CsvProvider(str(path))

    # Act
    result = provider.get_historical_bars("MNQ", idx[0].to_pydatetime(), idx[-1].to_pydatetime() + timedelta(minutes=5), "15m")

    # Assert
    assert len(result) == 10


def test_csv_provider_rejects_finer_interval_than_native_resolution(tmp_path):
    path, idx = _write_csv(tmp_path, freq="15min", n=10)
    provider = CsvProvider(str(path))

    with pytest.raises(DataProviderError):
        provider.get_historical_bars("MNQ", idx[0].to_pydatetime(), idx[-1].to_pydatetime() + timedelta(minutes=15), "1m")


class _FakeYfModule:
    def __init__(self, frame):
        self._frame = frame
        self.download_calls = []

    def download(self, tickers, start, end, interval, auto_adjust, progress):
        self.download_calls.append((tickers, start, end, interval))
        return self._frame


def test_yfinance_provider_raises_before_calling_api_when_start_predates_retention():
    # Arrange: 5m retention is ~60 days; request something far older
    fake_yf = _FakeYfModule(pd.DataFrame())
    provider = YFinanceProvider(yf_module=fake_yf)
    old_start = datetime.now(timezone.utc) - timedelta(days=400)

    # Act / Assert
    with pytest.raises(DataProviderError):
        provider.get_historical_bars("MNQ=F", old_start, datetime.now(timezone.utc), "5m")
    assert fake_yf.download_calls == []  # never even called the API


def test_yfinance_provider_normalizes_columns_and_timezone():
    # Arrange
    idx = pd.date_range("2026-06-01 09:30", periods=3, freq="5min")  # naive, ET-local
    frame = pd.DataFrame(
        {"Open": [1, 2, 3], "High": [1.1, 2.1, 3.1], "Low": [0.9, 1.9, 2.9], "Close": [1.05, 2.05, 3.05], "Volume": [10, 20, 30]},
        index=idx,
    )
    fake_yf = _FakeYfModule(frame)
    provider = YFinanceProvider(yf_module=fake_yf)

    # Act
    result = provider.get_historical_bars("MNQ=F", datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc), "5m")

    # Assert
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]
    assert str(result.index.tz) == "UTC"


def test_yfinance_provider_raises_when_api_returns_empty():
    fake_yf = _FakeYfModule(pd.DataFrame())
    provider = YFinanceProvider(yf_module=fake_yf)

    with pytest.raises(DataProviderError):
        provider.get_historical_bars("MNQ=F", datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc), "5m")


class _FakeDatabentoData:
    def __init__(self, frame):
        self._frame = frame

    def to_df(self):
        return self._frame


class _FakeDatabentoTimeseries:
    def __init__(self, frame):
        self._frame = frame
        self.calls = []

    def get_range(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeDatabentoData(self._frame)


class _FakeDatabentoClient:
    def __init__(self, frame):
        self.timeseries = _FakeDatabentoTimeseries(frame)


def test_databento_provider_resamples_1m_bars_to_requested_interval():
    # Arrange: 15 minutes of 1m bars -> requesting 5m should yield 3 bars
    idx = pd.date_range("2026-06-01 09:30", periods=15, freq="1min", tz="UTC")
    frame = pd.DataFrame(
        {"open": range(15), "high": range(15), "low": range(15), "close": range(15), "volume": [1] * 15}, index=idx
    )
    client = _FakeDatabentoClient(frame)
    provider = DatabentoProvider(client=client)

    # Act
    result = provider.get_historical_bars("MNQ", idx[0].to_pydatetime(), idx[-1].to_pydatetime(), "5m")

    # Assert
    assert len(result) == 3
    assert client.timeseries.calls[0]["symbols"] == ["MNQ.c.0"]


def test_databento_provider_raises_when_api_key_missing_on_first_fetch(monkeypatch):
    # Client construction (and the key check) is deferred to first actual
    # fetch -- so a request fully served by CachingProvider from disk never
    # needs a real key -- but an uncached fetch still must fail loudly.
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    provider = DatabentoProvider()

    with pytest.raises(DataProviderError):
        provider.get_historical_bars("MNQ", datetime(2024, 1, 1), datetime(2024, 1, 2), "5m")


def test_build_provider_unknown_name_raises():
    with pytest.raises(DataProviderError):
        build_provider("not_a_real_provider")


def test_build_provider_csv_requires_path():
    with pytest.raises(DataProviderError):
        build_provider("csv")


class _FakeMassiveResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class _FakeMassiveSession:
    def __init__(self, handler):
        self.calls = []
        self._handler = handler

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        status_code, payload = self._handler(url, params)
        return _FakeMassiveResponse(status_code, payload)


def _ns(ts) -> int:
    return int(pd.Timestamp(ts).value)


def test_massive_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)

    with pytest.raises(DataProviderError):
        MassiveProvider()


def test_massive_provider_filters_calendar_spread_tickers_out_of_contract_list():
    contracts_payload = {
        "results": [
            {"ticker": "MNQU6", "last_trade_date": "2026-09-18"},
            {"ticker": "MNQH0-MNQM1", "last_trade_date": "2026-09-18"},
        ]
    }
    session = _FakeMassiveSession(lambda url, params: (200, contracts_payload))
    provider = MassiveProvider(api_key="fake", session=session, min_request_interval_sec=0)

    contracts = provider._list_contracts("MNQ")

    assert [ticker for ticker, _ in contracts] == ["MNQU6"]


def test_massive_provider_rolls_across_two_contracts_and_stitches_bars():
    bar1 = {
        "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10,
        "window_start": _ns(pd.Timestamp("2026-03-01", tz="UTC")),
    }
    bar2 = {
        "open": 200, "high": 201, "low": 199, "close": 200.5, "volume": 20,
        "window_start": _ns(pd.Timestamp("2026-04-01", tz="UTC")),
    }
    contracts_payload = {
        "results": [
            {"ticker": "MNQH6", "last_trade_date": "2026-03-20"},
            {"ticker": "MNQM6", "last_trade_date": "2026-06-19"},
        ]
    }

    def handler(url, params):
        if "contracts" in url:
            return 200, contracts_payload
        if "MNQH6" in url:
            return 200, {"results": [bar1], "next_url": None}
        if "MNQM6" in url:
            return 200, {"results": [bar2], "next_url": None}
        raise AssertionError(f"unexpected url {url}")

    session = _FakeMassiveSession(handler)
    provider = MassiveProvider(api_key="fake", session=session, min_request_interval_sec=0)

    result = provider.get_historical_bars("MNQ", datetime(2026, 3, 1), datetime(2026, 4, 2), "5m")

    assert len(result) == 2
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]
    queried = [c["url"] for c in session.calls if "aggs" in c["url"]]
    assert any("MNQH6" in u for u in queried)
    assert any("MNQM6" in u for u in queried)


def test_massive_provider_follows_pagination_next_url():
    page1_bar = {
        "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
        "window_start": _ns(pd.Timestamp("2026-03-01", tz="UTC")),
    }
    page2_bar = {
        "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1,
        "window_start": _ns(pd.Timestamp("2026-03-02", tz="UTC")),
    }
    contracts_payload = {"results": [{"ticker": "MNQH6", "last_trade_date": "2026-03-20"}]}
    call_count = {"aggs": 0}

    def handler(url, params):
        if "contracts" in url:
            return 200, contracts_payload
        call_count["aggs"] += 1
        if call_count["aggs"] == 1:
            return 200, {"results": [page1_bar], "next_url": "https://api.polygon.io/futures/v1/aggs/MNQH6?cursor=abc"}
        return 200, {"results": [page2_bar], "next_url": None}

    session = _FakeMassiveSession(handler)
    provider = MassiveProvider(api_key="fake", session=session, min_request_interval_sec=0)

    result = provider.get_historical_bars("MNQ", datetime(2026, 3, 1), datetime(2026, 3, 5), "5m")

    assert len(result) == 2
    assert call_count["aggs"] == 2


def test_massive_provider_warns_when_real_data_starts_later_than_requested():
    # Arrange: contract is listed as covering the whole requested range, but
    # the aggregates endpoint only actually has bars starting well after
    # the requested start (mirrors the real MNQ/Massive 2024-07-09 floor).
    bar = {
        "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10,
        "window_start": _ns(pd.Timestamp("2026-03-15", tz="UTC")),
    }
    contracts_payload = {"results": [{"ticker": "MNQH6", "last_trade_date": "2026-03-20"}]}

    def handler(url, params):
        if "contracts" in url:
            return 200, contracts_payload
        return 200, {"results": [bar], "next_url": None}

    session = _FakeMassiveSession(handler)
    provider = MassiveProvider(api_key="fake", session=session, min_request_interval_sec=0)

    with pytest.warns(RuntimeWarning, match="no MNQ bars before"):
        provider.get_historical_bars("MNQ", datetime(2026, 3, 1), datetime(2026, 3, 20), "5m")


def test_massive_provider_does_not_warn_when_data_starts_at_requested_range():
    bar = {
        "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10,
        "window_start": _ns(pd.Timestamp("2026-03-01", tz="UTC")),
    }
    contracts_payload = {"results": [{"ticker": "MNQH6", "last_trade_date": "2026-03-20"}]}

    def handler(url, params):
        if "contracts" in url:
            return 200, contracts_payload
        return 200, {"results": [bar], "next_url": None}

    session = _FakeMassiveSession(handler)
    provider = MassiveProvider(api_key="fake", session=session, min_request_interval_sec=0)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        provider.get_historical_bars("MNQ", datetime(2026, 3, 1), datetime(2026, 3, 20), "5m")


def test_massive_provider_raises_on_non_200_response():
    session = _FakeMassiveSession(lambda url, params: (403, {"error": "forbidden"}))
    provider = MassiveProvider(api_key="fake", session=session, min_request_interval_sec=0)

    with pytest.raises(DataProviderError):
        provider.get_historical_bars("MNQ", datetime(2026, 3, 1), datetime(2026, 3, 5), "5m")


def test_massive_provider_raises_when_no_contracts_found():
    session = _FakeMassiveSession(lambda url, params: (200, {"results": []}))
    provider = MassiveProvider(api_key="fake", session=session, min_request_interval_sec=0)

    with pytest.raises(DataProviderError):
        provider.get_historical_bars("MNQ", datetime(2026, 3, 1), datetime(2026, 3, 5), "5m")


def test_build_provider_massive_constructs_without_a_network_call():
    provider = build_provider("massive", api_key="fake")
    assert isinstance(provider, MassiveProvider)


def test_build_provider_cache_true_wraps_in_caching_provider():
    from mnq_system.data.cache import CachingProvider

    provider = build_provider("massive", cache=True, api_key="fake")

    assert isinstance(provider, CachingProvider)


def test_build_provider_cache_false_by_default():
    provider = build_provider("massive", api_key="fake")
    assert isinstance(provider, MassiveProvider)


def test_build_provider_never_wraps_csv_in_cache_since_it_is_already_local(tmp_path):
    from mnq_system.data.cache import CachingProvider

    csv_path = tmp_path / "bars.csv"
    idx = pd.date_range("2026-01-01", periods=2, freq="5min", tz="UTC")
    pd.DataFrame(
        {"timestamp": idx, "open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2], "volume": [1, 1]}
    ).to_csv(csv_path, index=False)

    provider = build_provider("csv", cache=True, path=str(csv_path))

    assert not isinstance(provider, CachingProvider)
