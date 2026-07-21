import pandas as pd
import pytest

from mnq_system.backtest.engine import BacktestResult, TradeRecord
from mnq_system.backtest.stats import (
    SMALL_SAMPLE_THRESHOLD,
    UNIVERSAL_BREAKDOWN_DIMENSIONS,
    _max_drawdown_dollars,
    _max_drawdown_pct,
    bootstrap_confidence,
    bootstrap_confidence_for_trades,
    breakdown_by,
    compute_stats,
    equal_time_windows,
    full_breakdown_report,
    split_in_sample_out_of_sample,
    trades_to_dataframe,
    walk_forward_consistency,
    walk_forward_windows,
)


def _trade(
    pnl, r_multiple, entry_time, exit_time=None, setup_type="pullback", direction="long",
    exit_reason="full_target", context=None,
):
    return TradeRecord(
        setup_type=setup_type,
        direction=direction,
        entry_time=entry_time,
        exit_time=exit_time or (entry_time + pd.Timedelta(minutes=15)),
        entry_price=100.0,
        exit_price=100.0 + pnl,
        contracts=1,
        pnl=pnl,
        r_multiple=r_multiple,
        exit_reason=exit_reason,
        context=context or {},
    )


def _equity_curve(values, start="2026-01-01 09:30", freq="1D", tz="UTC"):
    idx = pd.date_range(start, periods=len(values), freq=freq, tz=tz)
    return pd.Series(values, index=idx, name="equity")


def test_compute_stats_on_empty_trade_list_reports_zero_trades_and_small_sample_warning():
    result = BacktestResult(trades=[], equity_curve=_equity_curve([50_000.0]), final_equity=50_000.0)

    stats = compute_stats(result, starting_equity=50_000.0)

    assert stats.total_trades == 0
    assert stats.win_rate == 0.0
    assert stats.small_sample_warning is True


def test_compute_stats_win_rate_and_profit_factor():
    # Arrange: win, win, loss, win, loss (chronological) -> win_rate 3/5
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [
        _trade(300, 2.0, t0),
        _trade(200, 1.5, t0 + pd.Timedelta(hours=1)),
        _trade(-150, -1.0, t0 + pd.Timedelta(hours=2)),
        _trade(100, 1.0, t0 + pd.Timedelta(hours=3)),
        _trade(-50, -1.0, t0 + pd.Timedelta(hours=4)),
    ]
    equity = _equity_curve([50_000, 50_300, 50_500, 50_350, 50_450, 50_400])
    result = BacktestResult(trades=trades, equity_curve=equity, final_equity=50_400.0)

    stats = compute_stats(result, starting_equity=50_000.0)

    assert stats.total_trades == 5
    assert stats.win_rate == pytest.approx(0.6)
    assert stats.profit_factor == pytest.approx(600 / 200)
    assert stats.avg_win == pytest.approx(200.0)
    assert stats.avg_loss == pytest.approx(-100.0)
    assert stats.largest_win == 300
    assert stats.largest_loss == -150
    assert stats.total_pnl == pytest.approx(400.0)


def test_compute_stats_tracks_max_consecutive_win_and_loss_streaks():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [
        _trade(100, 1.0, t0),
        _trade(100, 1.0, t0 + pd.Timedelta(hours=1)),
        _trade(-50, -1.0, t0 + pd.Timedelta(hours=2)),
        _trade(100, 1.0, t0 + pd.Timedelta(hours=3)),
        _trade(-50, -1.0, t0 + pd.Timedelta(hours=4)),
    ]
    result = BacktestResult(trades=trades, equity_curve=_equity_curve([50_000, 50_200]), final_equity=50_200.0)

    stats = compute_stats(result, starting_equity=50_000.0)

    assert stats.max_consecutive_wins == 2
    assert stats.max_consecutive_losses == 1


def test_compute_stats_profit_factor_is_infinite_with_no_losses():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [_trade(100, 1.0, t0), _trade(50, 0.5, t0 + pd.Timedelta(hours=1))]
    result = BacktestResult(trades=trades, equity_curve=_equity_curve([50_000, 50_150]), final_equity=50_150.0)

    stats = compute_stats(result, starting_equity=50_000.0)

    assert stats.profit_factor == float("inf")


def test_compute_stats_flags_small_sample_below_threshold():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [_trade(10, 0.5, t0 + pd.Timedelta(hours=i)) for i in range(SMALL_SAMPLE_THRESHOLD - 1)]
    result = BacktestResult(trades=trades, equity_curve=_equity_curve([50_000, 50_010]), final_equity=50_010.0)

    stats = compute_stats(result, starting_equity=50_000.0)

    assert stats.small_sample_warning is True


def test_compute_stats_no_small_sample_warning_at_threshold():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [_trade(10, 0.5, t0 + pd.Timedelta(hours=i)) for i in range(SMALL_SAMPLE_THRESHOLD)]
    result = BacktestResult(trades=trades, equity_curve=_equity_curve([50_000, 50_010]), final_equity=50_010.0)

    stats = compute_stats(result, starting_equity=50_000.0)

    assert stats.small_sample_warning is False


def test_max_drawdown_pct_from_a_known_peak_to_trough():
    equity = _equity_curve([100.0, 120.0, 90.0, 110.0])  # peak 120 -> trough 90 = -25%
    assert _max_drawdown_pct(equity) == pytest.approx(-0.25)


def test_max_drawdown_dollars_from_a_known_peak_to_trough():
    equity = _equity_curve([100.0, 120.0, 90.0, 110.0])
    assert _max_drawdown_dollars(equity) == pytest.approx(-30.0)


def test_max_drawdown_is_zero_for_a_monotonically_rising_equity_curve():
    equity = _equity_curve([100.0, 110.0, 120.0])
    assert _max_drawdown_pct(equity) == pytest.approx(0.0)


def test_trades_to_dataframe_round_trips_expected_columns():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    df = trades_to_dataframe([_trade(100, 1.0, t0)])

    assert list(df.columns) == [
        "setup_type", "direction", "entry_time", "exit_time", "entry_price",
        "exit_price", "contracts", "pnl", "r_multiple", "exit_reason",
    ]
    assert len(df) == 1


def test_split_in_sample_out_of_sample_partitions_trades_by_entry_time():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    split = pd.Timestamp("2026-01-02 00:00", tz="UTC")
    trade_1 = _trade(100, 1.0, t0)  # before split -> in-sample
    trade_2 = _trade(-50, -1.0, t0 + pd.Timedelta(days=2))  # after split -> out-of-sample
    trades = [trade_1, trade_2]

    # Equity curve points bracket each trade's fill so pre/post-split slices
    # land on the equity level actually in effect at that moment.
    equity = pd.Series(
        [50_000.0, 50_100.0, 50_100.0, 50_050.0],
        index=[t0, trade_1.exit_time, split, trade_2.exit_time],
    )
    result = BacktestResult(trades=trades, equity_curve=equity, final_equity=50_050.0)

    in_sample, oos = split_in_sample_out_of_sample(result, starting_equity=50_000.0, split_time=split)

    assert in_sample.total_trades == 1
    assert oos.total_trades == 1
    assert in_sample.total_pnl == pytest.approx(100.0)
    assert oos.total_pnl == pytest.approx(-50.0)


def test_trades_to_dataframe_flattens_context_dict_into_columns():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trade = _trade(100, 1.0, t0, context={"entry_weekday": "Thursday", "volatility_regime": "high"})

    df = trades_to_dataframe([trade])

    assert df.loc[0, "entry_weekday"] == "Thursday"
    assert df.loc[0, "volatility_regime"] == "high"


def test_breakdown_by_computes_per_bucket_stats():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [
        _trade(200, 2.0, t0, context={"entry_weekday": "Monday"}),
        _trade(-100, -1.0, t0 + pd.Timedelta(hours=1), context={"entry_weekday": "Monday"}),
        _trade(50, 1.0, t0 + pd.Timedelta(hours=2), context={"entry_weekday": "Tuesday"}),
    ]

    table = breakdown_by(trades, "entry_weekday")

    assert table.loc["Monday", "trades"] == 2
    assert table.loc["Monday", "win_rate"] == pytest.approx(0.5)
    assert table.loc["Monday", "total_pnl"] == pytest.approx(100.0)
    assert table.loc["Tuesday", "trades"] == 1
    assert table.loc["Tuesday", "win_rate"] == pytest.approx(1.0)


def test_breakdown_by_returns_empty_frame_for_unknown_context_key():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [_trade(100, 1.0, t0, context={"entry_weekday": "Monday"})]

    table = breakdown_by(trades, "nonexistent_key")

    assert table.empty


def test_breakdown_by_returns_empty_frame_for_no_trades():
    table = breakdown_by([], "entry_weekday")
    assert table.empty


def test_full_breakdown_report_defaults_to_universal_dimensions_only():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [
        _trade(100, 1.0, t0, direction="long", setup_type="pullback", context={
            "entry_weekday": "Monday", "entry_hour_et": 10, "bias": "bullish",
        }),
    ]

    report = full_breakdown_report(trades)

    # No strategy-specific fields (e.g. "bias") without being asked for them
    # -- stats.py has no knowledge of any particular strategy's context keys.
    assert set(report.keys()) == {"direction", "setup_type", "entry_weekday", "entry_hour_et"}
    assert report["direction"].loc["long", "trades"] == 1


def test_full_breakdown_report_includes_extra_strategy_specific_dimensions_when_given():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [
        _trade(100, 1.0, t0, direction="long", setup_type="pullback", context={
            "entry_weekday": "Monday", "entry_hour_et": 10, "bias": "bullish",
            "volatility_regime": "mid", "trend_regime": "trending",
        }),
    ]

    dimensions = UNIVERSAL_BREAKDOWN_DIMENSIONS + ["bias", "volatility_regime", "trend_regime"]
    report = full_breakdown_report(trades, dimensions=dimensions)

    assert set(report.keys()) == {
        "direction", "setup_type", "entry_weekday", "entry_hour_et", "bias", "volatility_regime", "trend_regime",
    }
    assert report["bias"].loc["bullish", "trades"] == 1


def test_bootstrap_confidence_on_empty_input():
    result = bootstrap_confidence([])
    assert result["n"] == 0
    assert result["mean"] != result["mean"]  # NaN


def test_bootstrap_confidence_on_single_value_is_a_degenerate_point():
    result = bootstrap_confidence([1.5])
    assert result["n"] == 1
    assert result["mean"] == pytest.approx(1.5)
    assert result["ci_low"] == pytest.approx(1.5)
    assert result["ci_high"] == pytest.approx(1.5)


def test_bootstrap_confidence_ci_excludes_zero_for_a_clearly_positive_sample():
    # Arrange: consistently positive R multiples with modest spread
    values = [0.8, 1.2, 0.9, 1.1, 1.0, 0.95, 1.05, 0.85, 1.15, 1.0] * 3

    # Act
    result = bootstrap_confidence(values)

    # Assert: CI comfortably above zero, near-zero probability the edge is <= 0
    assert result["ci_low"] > 0
    assert result["prob_mean_le_zero"] < 0.05


def test_bootstrap_confidence_ci_straddles_zero_for_a_noisy_small_sample():
    # Arrange: small, high-variance sample with a mix of signs
    values = [3.0, -1.0, -1.0, 2.0, -1.0]

    # Act
    result = bootstrap_confidence(values)

    # Assert: not enough evidence to rule out a zero-or-negative true mean
    assert result["ci_low"] < 0
    assert result["prob_mean_le_zero"] > 0.1


def test_bootstrap_confidence_is_deterministic_across_calls():
    values = [1.0, -0.5, 2.0, -1.0, 0.5, -0.2, 1.5]
    first = bootstrap_confidence(values)
    second = bootstrap_confidence(values)
    assert first == second


def test_bootstrap_confidence_for_trades_extracts_r_multiples():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [_trade(100, 1.0, t0), _trade(-50, -1.0, t0 + pd.Timedelta(hours=1))]

    result = bootstrap_confidence_for_trades(trades)

    assert result["n"] == 2
    assert result["mean"] == pytest.approx(0.0)


def test_breakdown_by_includes_bootstrap_confidence_columns():
    t0 = pd.Timestamp("2026-01-01 09:30", tz="UTC")
    trades = [
        _trade(100, 1.0, t0, context={"entry_weekday": "Monday"}),
        _trade(150, 1.2, t0 + pd.Timedelta(hours=1), context={"entry_weekday": "Monday"}),
    ]

    table = breakdown_by(trades, "entry_weekday")

    assert "avg_r_ci_low" in table.columns
    assert "avg_r_ci_high" in table.columns
    assert "prob_edge_le_zero" in table.columns
    assert table.loc["Monday", "avg_r_ci_low"] <= table.loc["Monday", "avg_r"] <= table.loc["Monday", "avg_r_ci_high"]


def test_equal_time_windows_produces_n_plus_one_evenly_spaced_boundaries():
    start = pd.Timestamp("2022-01-01", tz="UTC")
    end = pd.Timestamp("2026-01-01", tz="UTC")

    boundaries = equal_time_windows(start, end, n_windows=4)

    assert len(boundaries) == 5
    assert boundaries[0] == start
    assert boundaries[-1] == end
    # Equal-length by duration, not necessarily aligned to calendar years
    # (4 years split 4 ways isn't exactly 1 year each once leap days are involved).
    assert abs((boundaries[1] - pd.Timestamp("2023-01-01", tz="UTC")).total_seconds()) < 86400


def test_walk_forward_windows_partitions_trades_chronologically_with_no_overlap():
    t0 = pd.Timestamp("2022-01-01", tz="UTC")
    trades = [
        _trade(100, 1.0, t0 + pd.Timedelta(days=10)),  # window 0
        _trade(-50, -1.0, t0 + pd.Timedelta(days=400)),  # window 1
        _trade(200, 2.0, t0 + pd.Timedelta(days=410)),  # window 1
    ]
    boundaries = equal_time_windows(t0, t0 + pd.Timedelta(days=730), n_windows=2)

    table = walk_forward_windows(trades, boundaries)

    assert len(table) == 2
    assert table.iloc[0]["trades"] == 1
    assert table.iloc[1]["trades"] == 2
    assert table.iloc[1]["total_pnl"] == pytest.approx(150.0)


def test_walk_forward_windows_reports_zero_trades_for_an_empty_window():
    t0 = pd.Timestamp("2022-01-01", tz="UTC")
    trades = [_trade(100, 1.0, t0 + pd.Timedelta(days=10))]  # only in window 0
    boundaries = equal_time_windows(t0, t0 + pd.Timedelta(days=730), n_windows=2)

    table = walk_forward_windows(trades, boundaries)

    assert table.iloc[1]["trades"] == 0
    assert table.iloc[1]["total_pnl"] == 0.0
    assert pd.isna(table.iloc[1]["profit_factor"])


def test_walk_forward_consistency_flags_a_filter_that_only_works_in_one_window():
    # Arrange: window 0 has a great PF, window 1 has a bad PF -- inconsistent edge
    table = pd.DataFrame(
        [
            {"trades": 20, "win_rate": 0.6, "avg_r": 0.5, "profit_factor": 2.0, "total_pnl": 1000.0},
            {"trades": 20, "win_rate": 0.3, "avg_r": -0.3, "profit_factor": 0.5, "total_pnl": -500.0},
        ]
    )

    summary = walk_forward_consistency(table)

    assert summary["windows"] == 2
    assert summary["frac_windows_pf_above_1"] == pytest.approx(0.5)
    assert summary["std_pf"] > 0


def test_walk_forward_consistency_full_marks_for_a_filter_that_holds_in_every_window():
    table = pd.DataFrame(
        [
            {"trades": 20, "win_rate": 0.55, "avg_r": 0.2, "profit_factor": 1.3, "total_pnl": 500.0},
            {"trades": 20, "win_rate": 0.5, "avg_r": 0.15, "profit_factor": 1.2, "total_pnl": 400.0},
        ]
    )

    summary = walk_forward_consistency(table)

    assert summary["frac_windows_pf_above_1"] == pytest.approx(1.0)


def test_walk_forward_consistency_ignores_empty_windows():
    table = pd.DataFrame(
        [
            {"trades": 20, "win_rate": 0.55, "avg_r": 0.2, "profit_factor": 1.3, "total_pnl": 500.0},
            {"trades": 0, "win_rate": float("nan"), "avg_r": float("nan"), "profit_factor": float("nan"), "total_pnl": 0.0},
        ]
    )

    summary = walk_forward_consistency(table)

    assert summary["windows"] == 1
