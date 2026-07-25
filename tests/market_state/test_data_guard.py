"""Hold-out boundary guard — must refuse hold-out access without explicit opt-in,
BEFORE any data fetch happens."""
import pytest

from market_state.data import HoldoutAccessError, load_bars


def test_holdout_blocked_without_optin():
    with pytest.raises(HoldoutAccessError):
        load_bars(split="holdout")            # no allow_holdout -> raises before fetch


def test_all_split_blocked_without_optin():
    with pytest.raises(HoldoutAccessError):
        load_bars(split="all")


def test_unknown_split_rejected():
    with pytest.raises(ValueError):
        load_bars(split="everything")
