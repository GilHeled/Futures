import pandas as pd
import pytest

from mnq_system.session_features import overnight_signed_volume_imbalance_by_date, prior_session_close_by_date

_UP = (100.0, 100.5, 99.5, 100.2)  # close > open
_DOWN = (100.0, 100.5, 99.0, 99.5)  # close < open


def _make_bars(specs, tz="America/New_York"):
    idx = pd.DatetimeIndex([pd.Timestamp(s[0], tz=tz) for s in specs])
    return pd.DataFrame(
        {
            "open": [s[1] for s in specs], "high": [s[2] for s in specs],
            "low": [s[3] for s in specs], "close": [s[4] for s in specs],
            "volume": [s[5] for s in specs],
        },
        index=idx,
    )


def test_prior_session_close_by_date_uses_last_bar_before_close_time():
    specs = [
        ("2026-06-01 15:50", 100.0, 100.3, 99.8, 100.0, 1000),
        ("2026-06-01 15:55", 100.0, 100.4, 99.9, 100.2, 1000),  # last bar before 16:00 -> reference for 06-02
        ("2026-06-01 16:00", 100.2, 100.3, 100.0, 100.1, 1000),  # at/after close time, excluded
        ("2026-06-02 09:30", 101.0, 101.5, 100.8, 101.2, 1000),
    ]
    bars = _make_bars(specs)

    result = prior_session_close_by_date(bars, "America/New_York")

    import datetime
    assert result[datetime.date(2026, 6, 2)] == pytest.approx(100.2)
    assert datetime.date(2026, 6, 1) not in result  # no prior data for the very first day


def test_overnight_signed_volume_imbalance_sums_only_overnight_bars():
    specs = [
        ("2026-06-01 12:00", *_UP[:4], 5000),  # RTH bar -- excluded from the overnight window
        ("2026-06-01 16:00", *_UP[:4], 1000),  # overnight -> feeds 2026-06-02
        ("2026-06-01 20:00", *_DOWN[:4], 300),  # overnight -> feeds 2026-06-02
        ("2026-06-02 09:25", *_UP[:4], 200),  # still before 09:30 -> feeds 2026-06-02
        ("2026-06-02 09:30", *_UP[:4], 1000),  # entry bar itself -- excluded (not < 09:30)
    ]
    bars = _make_bars(specs)

    result = overnight_signed_volume_imbalance_by_date(bars, "America/New_York")

    import datetime
    expected = 1000 - 300 + 200  # up(+1000) + down(-300) + up(+200); the RTH and entry bars excluded
    assert result[datetime.date(2026, 6, 2)] == pytest.approx(expected)
