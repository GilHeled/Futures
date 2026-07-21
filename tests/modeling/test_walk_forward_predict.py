"""
Tests for the extracted walk_forward_predict/walk_forward_oos_proba
functions (mnq_system.modeling.evaluate) -- the single canonical
retrain-on-expanding-window, predict-out-of-sample implementation shared
by evaluate_horizon (research metrics) and mnq_system.strategies.model_driven
(a live Strategy trading on the same predictions).
"""

import numpy as np
import pandas as pd

from mnq_system.modeling.evaluate import evaluate_horizon, walk_forward_oos_proba, walk_forward_predict


def _make_signal_data(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    idx = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)}, index=idx)
    labels = pd.Series(label, index=idx)
    return features, labels


def test_walk_forward_oos_proba_is_nan_before_the_first_fold():
    features, labels = _make_signal_data()

    proba = walk_forward_oos_proba(features, labels, n_folds=5, min_train_fraction=0.2)

    n_reserved = int(len(features) * 0.2)
    assert proba.iloc[: n_reserved - 1].isna().all(axis=None)
    assert proba.iloc[n_reserved:].notna().any(axis=None)


def test_walk_forward_oos_proba_rows_sum_to_one_where_defined():
    features, labels = _make_signal_data()

    proba = walk_forward_oos_proba(features, labels, n_folds=5, min_train_fraction=0.2)

    defined = proba.dropna()
    np.testing.assert_allclose(defined.sum(axis=1).to_numpy(), 1.0, atol=1e-8)


def test_walk_forward_predict_returns_matching_fold_metadata():
    features, labels = _make_signal_data()

    result = walk_forward_predict(features, labels, n_folds=5, min_train_fraction=0.2)

    assert len(result.fold_coefs) == len(result.fold_meta)
    assert len(result.fold_meta) > 0
    total_test_rows = sum(meta["n_test"] for meta in result.fold_meta)
    assert result.proba.notna().all(axis=1).sum() == total_test_rows


def test_walk_forward_predict_a_row_is_unaffected_by_changing_rows_strictly_after_it():
    features, labels = _make_signal_data()
    cutoff = len(features) - 50  # well inside the last fold's test window

    result_a = walk_forward_predict(features, labels, n_folds=5, min_train_fraction=0.2)

    features_b = features.copy()
    labels_b = labels.copy()
    features_b.iloc[cutoff:] = features_b.iloc[cutoff:] * 1000 + 500  # wildly different future rows
    labels_b.iloc[cutoff:] = 1 - labels_b.iloc[cutoff:]
    result_b = walk_forward_predict(features_b, labels_b, n_folds=5, min_train_fraction=0.2)

    pd.testing.assert_frame_equal(result_a.proba.iloc[:cutoff], result_b.proba.iloc[:cutoff])


def test_evaluate_horizon_still_matches_walk_forward_predict_directly():
    # Regression check for the evaluate_horizon <- walk_forward_predict
    # extraction: evaluate_horizon's OOS log-loss must be computable
    # directly from walk_forward_predict's own output.
    features, labels = _make_signal_data(seed=1)

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5)
    wf = walk_forward_predict(features, labels, n_folds=5, min_train_fraction=0.2)

    valid_mask = wf.proba.notna().all(axis=1)
    assert result.n_test == int(valid_mask.sum())
