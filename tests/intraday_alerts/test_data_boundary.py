import numpy as np
import pandas as pd
import pytest

from intraday_alerts import config as C
from intraday_alerts.data import HoldoutAccessError, annotate_session, load_bars


def test_holdout_guard_blocks_without_optin():
    # must raise BEFORE any data fetch — the boundary guard
    with pytest.raises(HoldoutAccessError):
        load_bars("MES", split="holdout")
    with pytest.raises(HoldoutAccessError):
        load_bars("MES", split="all")


def test_dev_split_is_the_default():
    # default split is dev (never touches the locked hold-out) — no exception path
    assert "dev" == load_bars.__defaults__[0]


def test_boundary_dates_frozen():
    # the locked hold-out boundary is exactly the pre-registered one
    assert C.HOLDOUT_START == pd.Timestamp("2025-01-01", tz="UTC")
    assert C.DEV_END < C.HOLDOUT_START


def test_annotate_session_entry_window():
    # 5-min bars across one ET day; entry_eligible only within 10:00–15:00 ET
    idx = pd.date_range("2024-06-03 13:30", "2024-06-03 20:30", freq="5min", tz="UTC")  # 09:30–16:30 ET
    bars = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=idx)
    ann = annotate_session(bars)
    et = idx.tz_convert(C.TIMEZONE)
    for t, eligible in zip(et, ann["entry_eligible"]):
        expect = (t.time() >= pd.Timestamp("10:00").time()) and (t.time() < pd.Timestamp("15:00").time())
        assert bool(eligible) == expect
    # nothing before 10:00 ET is entry-eligible (opening range not yet formed)
    assert not ann.loc[ann.index.tz_convert(C.TIMEZONE).time < pd.Timestamp("10:00").time(), "entry_eligible"].any()
