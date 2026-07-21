"""
Tests for the diagnostic layer added on top of the core walk-forward
evaluation (mnq_system.modeling.evaluate): Brier score, calibration error,
coefficient stability across folds, and regime/year-conditional breakdowns.
"""

import numpy as np
import pandas as pd
import pytest

from mnq_system.modeling.evaluate import (
    REGIME_COLUMN,
    evaluate_horizon,
    expected_calibration_error,
    per_row_brier_score,
)


def test_per_row_brier_score_matches_manual_calculation():
    y_true = np.array([0, 1])
    proba = np.array([[0.8, 0.2], [0.3, 0.7]])
    classes = np.array([0, 1])

    result = per_row_brier_score(y_true, proba, classes)

    # row0: (0.8-1)^2 + (0.2-0)^2 = 0.08 ; row1: (0.3-0)^2 + (0.7-1)^2 = 0.18
    np.testing.assert_allclose(result, [0.08, 0.18])


def test_expected_calibration_error_is_zero_for_perfect_calibration():
    table = pd.DataFrame(
        {"mean_predicted": [0.2, 0.8], "actual_frequency": [0.2, 0.8], "n": [100, 100], "class": [0, 0]}
    )

    result = expected_calibration_error(table)

    assert result.loc[result["class"] == 0, "ece"].iloc[0] == pytest.approx(0.0)


def test_expected_calibration_error_is_positive_when_miscalibrated():
    table = pd.DataFrame(
        {"mean_predicted": [0.2, 0.9], "actual_frequency": [0.5, 0.5], "n": [100, 100], "class": [0, 0]}
    )

    result = expected_calibration_error(table)

    assert result.loc[result["class"] == 0, "ece"].iloc[0] > 0


def test_expected_calibration_error_handles_empty_table():
    result = expected_calibration_error(pd.DataFrame())

    assert result.empty


def test_evaluate_horizon_reports_brier_improvement_for_a_planted_signal():
    rng = np.random.default_rng(3)
    n = 3000
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)})
    labels = pd.Series(label)

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)

    assert result.brier_ci_low > 0
    assert result.model_brier < result.baseline_brier


def test_evaluate_horizon_reports_coefficient_stability_across_folds():
    rng = np.random.default_rng(4)
    n = 3000
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)})
    labels = pd.Series(label)

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)

    assert not result.coefficient_stability.empty
    signal_rows = result.coefficient_stability[result.coefficient_stability["feature"] == "signal"]
    # A genuine, strong planted signal should have a highly sign-consistent coefficient across folds.
    assert (signal_rows["sign_consistency"] >= 0.8).all()


def test_evaluate_horizon_computes_calibration_error_when_calibration_available():
    rng = np.random.default_rng(5)
    n = 3000
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)})
    labels = pd.Series(label)

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)

    assert not result.calibration_error.empty
    assert (result.calibration_error["ece"] >= 0).all()


def test_evaluate_horizon_skips_regime_breakdown_when_column_absent():
    rng = np.random.default_rng(6)
    n = 500
    features = pd.DataFrame({"x": rng.normal(size=n)})
    labels = pd.Series(rng.integers(0, 2, size=n))
    assert REGIME_COLUMN not in features.columns

    result = evaluate_horizon(features, labels, horizon=1, n_folds=4)

    assert result.regime_breakdown.empty


def test_evaluate_horizon_computes_regime_breakdown_when_column_present():
    rng = np.random.default_rng(7)
    n = 4000
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    regime_pctile = rng.uniform(0, 1, size=n)
    features = pd.DataFrame({"signal": signal, REGIME_COLUMN: regime_pctile})
    labels = pd.Series(label)

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)

    assert not result.regime_breakdown.empty
    assert set(result.regime_breakdown["bucket"]) <= {"low_vol", "mid_vol", "high_vol"}


def test_evaluate_horizon_computes_year_breakdown():
    n = 4000
    rng = np.random.default_rng(8)
    idx = pd.date_range("2019-01-01", periods=n, freq="1D", tz="UTC")  # ~11 years -- spans multiple calendar years
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)}, index=idx)
    labels = pd.Series(label, index=idx)

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)

    assert not result.year_breakdown.empty
    assert result.year_breakdown["bucket"].nunique() > 1  # spans multiple calendar years
