import numpy as np
import pandas as pd

from mnq_system.modeling.evaluate import evaluate_horizon, expanding_folds, per_row_neg_log_likelihood


def test_expanding_folds_never_lets_train_see_test_or_later_data():
    folds = expanding_folds(n=100, n_folds=5, min_train_fraction=0.2)

    assert len(folds) == 5
    for train_slice, test_slice in folds:
        assert train_slice.stop == test_slice.start  # train ends exactly where test begins
        assert train_slice.start == 0

    # Test windows are chronological and non-overlapping, covering the tail of the series.
    for (_, test_a), (_, test_b) in zip(folds, folds[1:]):
        assert test_a.stop == test_b.start


def test_expanding_folds_reserves_a_minimum_training_set():
    folds = expanding_folds(n=100, n_folds=4, min_train_fraction=0.3)

    assert folds[0][1].start == 30  # first test fold starts only after the 30% reserved training set


def test_per_row_neg_log_likelihood_matches_manual_calculation():
    y_true = np.array([0, 1, 0])
    proba = np.array([[0.8, 0.2], [0.3, 0.7], [0.5, 0.5]])
    classes = np.array([0, 1])

    result = per_row_neg_log_likelihood(y_true, proba, classes)

    expected = -np.log([0.8, 0.7, 0.5])
    np.testing.assert_allclose(result, expected)


def test_model_with_a_planted_signal_beats_the_naive_baseline():
    # A feature that near-perfectly determines the label, buried among pure
    # noise columns -- proves the harness can actually detect a real signal
    # before trusting it on real market data.
    rng = np.random.default_rng(0)
    n = 3000
    signal = rng.normal(size=n)
    noise1 = rng.normal(size=n)
    noise2 = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    features = pd.DataFrame({"signal": signal, "noise1": noise1, "noise2": noise2})
    labels = pd.Series(label)

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)

    assert result.ci_low > 0  # clearly beats chance
    assert result.model_log_loss < result.baseline_log_loss


def test_model_on_pure_noise_does_not_show_a_spurious_improvement():
    rng = np.random.default_rng(1)
    n = 3000
    features = pd.DataFrame(
        {"noise1": rng.normal(size=n), "noise2": rng.normal(size=n), "noise3": rng.normal(size=n)}
    )
    labels = pd.Series(rng.integers(0, 2, size=n))  # independent of every feature

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)

    assert result.ci_low <= 0  # must not claim a confident improvement out of pure noise


def test_evaluate_horizon_drops_rows_with_missing_features_or_labels():
    n = 500
    rng = np.random.default_rng(2)
    features = pd.DataFrame({"x": rng.normal(size=n)})
    labels = pd.Series(rng.integers(0, 2, size=n).astype(float))
    features.iloc[0, 0] = float("nan")
    labels.iloc[1] = float("nan")

    result = evaluate_horizon(features, labels, horizon=1, n_folds=4)

    assert result.n_test <= n - 2  # at least the two invalid rows are excluded from every fold's test set
