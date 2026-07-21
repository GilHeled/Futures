import pandas as pd
import pytest

from mnq_system.backtest.excursion import compute_trade_excursion, near_miss_after_stop


def _bars(rows):
    idx = pd.date_range("2026-01-01 09:00", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(
        {"open": [r[0] for r in rows], "high": [r[1] for r in rows], "low": [r[2] for r in rows], "close": [r[3] for r in rows]},
        index=idx,
    )


def test_compute_trade_excursion_long_measures_best_and_worst_from_entry():
    # Arrange: entry at 100, then dips to 97 (MAE=3), rallies to 106 (MFE=6), exits at bar 3
    bars = _bars([(100, 101, 99, 100), (99, 100, 97, 98), (98, 106, 98, 105), (105, 106, 104, 105)])

    # Act
    result = compute_trade_excursion(bars, "long", entry_price=100.0, entry_time=bars.index[0], exit_time=bars.index[3])

    # Assert
    assert result["mfe"] == pytest.approx(6.0)
    assert result["mae"] == pytest.approx(3.0)


def test_compute_trade_excursion_short_mirrors_long():
    # Arrange: entry at 100 (short), price rallies to 104 first (MAE=4), then drops to 92 (MFE=8)
    bars = _bars([(100, 104, 99, 103), (103, 104, 92, 93)])

    # Act
    result = compute_trade_excursion(bars, "short", entry_price=100.0, entry_time=bars.index[0], exit_time=bars.index[1])

    # Assert
    assert result["mfe"] == pytest.approx(8.0)
    assert result["mae"] == pytest.approx(4.0)


def test_compute_trade_excursion_is_zero_for_a_flat_single_bar():
    bars = _bars([(100, 100, 100, 100)])
    result = compute_trade_excursion(bars, "long", entry_price=100.0, entry_time=bars.index[0], exit_time=bars.index[0])
    assert result == {"mfe": 0.0, "mae": 0.0}


def test_compute_trade_excursion_empty_window_returns_zero():
    bars = _bars([(100, 100, 100, 100)])
    later = bars.index[0] + pd.Timedelta(days=1)
    result = compute_trade_excursion(bars, "long", entry_price=100.0, entry_time=later, exit_time=later + pd.Timedelta(minutes=5))
    assert result == {"mfe": 0.0, "mae": 0.0}


def test_near_miss_after_stop_detects_full_target_reached_later():
    # Arrange: stopped out long at bar 0; target is 10 points above entry (110);
    # price later reaches 111, comfortably past the target.
    bars = _bars([(100, 100, 100, 100), (100, 105, 99, 104), (104, 111, 103, 110)])
    stop_time = bars.index[0]
    lookahead_end = bars.index[-1]

    # Act
    result = near_miss_after_stop(bars, "long", entry_price=100.0, target_1=110.0, stop_time=stop_time, lookahead_end=lookahead_end)

    # Assert
    assert result["reached_100pct"] is True
    assert result["reached_75pct"] is True
    assert result["reached_50pct"] is True
    assert result["best_pct_of_target"] >= 1.0


def test_near_miss_after_stop_detects_partial_progress_only():
    # Arrange: target is 10 points above entry; price only gets to 105 (50% of the way)
    bars = _bars([(100, 100, 100, 100), (100, 105, 99, 104)])
    result = near_miss_after_stop(
        bars, "long", entry_price=100.0, target_1=110.0, stop_time=bars.index[0], lookahead_end=bars.index[-1]
    )

    assert result["reached_50pct"] is True
    assert result["reached_75pct"] is False
    assert result["reached_100pct"] is False
    assert result["best_pct_of_target"] == pytest.approx(0.5)


def test_near_miss_after_stop_false_when_price_continues_against_the_trade():
    # Arrange: long stopped out, price keeps falling afterward -- no favorable excursion at all
    bars = _bars([(100, 100, 100, 100), (99, 99, 95, 96)])
    result = near_miss_after_stop(
        bars, "long", entry_price=100.0, target_1=110.0, stop_time=bars.index[0], lookahead_end=bars.index[-1]
    )

    assert result["reached_50pct"] is False
    assert result["best_pct_of_target"] == pytest.approx(0.0)


def test_near_miss_after_stop_mirrors_for_short():
    # Arrange: short stopped out; target is 10 points BELOW entry (90); price later reaches 89
    bars = _bars([(100, 100, 100, 100), (100, 101, 89, 90)])
    result = near_miss_after_stop(
        bars, "short", entry_price=100.0, target_1=90.0, stop_time=bars.index[0], lookahead_end=bars.index[-1]
    )

    assert result["reached_100pct"] is True


def test_near_miss_after_stop_empty_lookahead_window_returns_false():
    bars = _bars([(100, 100, 100, 100)])
    result = near_miss_after_stop(
        bars, "long", entry_price=100.0, target_1=110.0, stop_time=bars.index[0], lookahead_end=bars.index[0]
    )
    assert result["reached_50pct"] is False
    assert result["best_pct_of_target"] == 0.0
