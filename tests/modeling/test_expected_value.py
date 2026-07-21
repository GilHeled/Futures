"""
Tests for causal_expected_value (mnq_system.modeling.evaluate) -- turns the
model's full predicted class-probability distribution into a continuous
expected-value estimate (and a matching predictive variance), rather than
reducing to a top-1 class first. Also regression-tests the new
`train_index` key on walk_forward_predict's fold_meta this function relies on.
"""

import numpy as np
import pandas as pd

from mnq_system.modeling.evaluate import (
    WalkForwardPredictions,
    causal_expected_value,
    walk_forward_predict,
)


def _make_signal_data(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    idx = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)}, index=idx)
    labels = pd.Series(label, index=idx)
    return features, labels


def test_train_index_is_present_and_strictly_before_its_own_test_index():
    features, labels = _make_signal_data()

    result = walk_forward_predict(features, labels, n_folds=5, min_train_fraction=0.2)

    for meta in result.fold_meta:
        assert len(meta["train_index"]) == meta["n_train"]
        assert meta["train_index"].max() < meta["test_index"].min()


def test_ev_is_nan_before_the_first_fold_begins():
    n = 200
    idx = pd.date_range("2020-01-01", periods=n, freq="5min", tz="UTC")
    proba = pd.DataFrame(np.nan, index=idx, columns=[0, 1])
    # fold 0's own reserved training prefix is rows [0:50); its test window
    # is [50:100). fold 1 trains on everything before it, tests [100:200).
    proba.loc[idx[50:200], 0] = 0.5
    proba.loc[idx[50:200], 1] = 0.5
    fold_meta = [
        {"fold": 0, "test_index": idx[50:100], "train_index": idx[0:50], "n_train": 50, "n_test": 50},
        {"fold": 1, "test_index": idx[100:200], "train_index": idx[0:100], "n_train": 100, "n_test": 100},
    ]
    wf = WalkForwardPredictions(classes=np.array([0, 1]), proba=proba, baseline_proba=proba.copy(), fold_coefs=[], fold_meta=fold_meta)

    labels = pd.Series(([0] * 25 + [1] * 25) * 4, index=idx)
    continuous_return = pd.Series(0.0, index=idx)

    result = causal_expected_value(wf, labels, continuous_return)

    assert result["ev"].iloc[:50].isna().all()
    assert result["ev"].iloc[50:].notna().all()


def test_ev_exactly_recovers_known_class_returns():
    n = 200
    idx = pd.date_range("2020-01-01", periods=n, freq="5min", tz="UTC")
    proba = pd.DataFrame(np.nan, index=idx, columns=[0, 1])
    # fold 0: train on rows [0:100), test on [100:150)
    train_index, test_index = idx[0:100], idx[100:150]
    proba.loc[test_index, 0] = 0.3
    proba.loc[test_index, 1] = 0.7
    fold_meta = [{"fold": 0, "test_index": test_index, "train_index": train_index, "n_train": 100, "n_test": 50}]
    wf = WalkForwardPredictions(classes=np.array([0, 1]), proba=proba, baseline_proba=proba.copy(), fold_coefs=[], fold_meta=fold_meta)

    # Deterministic training data: class 0 always returns +2.0, class 1 always returns -3.0
    labels = pd.Series(0, index=idx)
    labels.loc[train_index[::2]] = 0
    labels.loc[train_index[1::2]] = 1
    continuous_return = pd.Series(0.0, index=idx)
    continuous_return.loc[labels == 0] = 2.0
    continuous_return.loc[labels == 1] = -3.0

    result = causal_expected_value(wf, labels, continuous_return)

    expected_ev = 0.3 * 2.0 + 0.7 * (-3.0)  # -1.5
    expected_var = 0.3 * (2.0 - expected_ev) ** 2 + 0.7 * (-3.0 - expected_ev) ** 2  # 5.25
    np.testing.assert_allclose(result.loc[test_index, "ev"].to_numpy(), expected_ev)
    np.testing.assert_allclose(result.loc[test_index, "variance"].to_numpy(), expected_var)


def test_a_class_absent_from_training_defaults_to_return_neutral_zero():
    n = 150
    idx = pd.date_range("2020-01-01", periods=n, freq="5min", tz="UTC")
    proba = pd.DataFrame(np.nan, index=idx, columns=[0, 1])
    train_index, test_index = idx[0:100], idx[100:150]
    proba.loc[test_index, 0] = 0.4
    proba.loc[test_index, 1] = 0.6
    fold_meta = [{"fold": 0, "test_index": test_index, "train_index": train_index, "n_train": 100, "n_test": 50}]
    wf = WalkForwardPredictions(classes=np.array([0, 1]), proba=proba, baseline_proba=proba.copy(), fold_coefs=[], fold_meta=fold_meta)

    # Training data is entirely class 0 -- class 1 never observed in training
    labels = pd.Series(0, index=idx)
    continuous_return = pd.Series(5.0, index=idx)  # class 0's known return

    result = causal_expected_value(wf, labels, continuous_return)

    expected_ev = 0.4 * 5.0 + 0.6 * 0.0  # class 1 defaults to 0.0
    np.testing.assert_allclose(result.loc[test_index, "ev"].to_numpy(), expected_ev)


def test_causality_changing_rows_strictly_after_a_cutoff_does_not_change_earlier_ev():
    n = 300
    idx = pd.date_range("2020-01-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(1)
    proba_vals = rng.uniform(0.2, 0.8, size=n)
    proba = pd.DataFrame({0: 1 - proba_vals, 1: proba_vals}, index=idx)

    fold_meta = [
        {"fold": 0, "test_index": idx[50:150], "train_index": idx[0:50], "n_train": 50, "n_test": 100},
        {"fold": 1, "test_index": idx[150:300], "train_index": idx[0:150], "n_train": 150, "n_test": 150},
    ]
    wf_a = WalkForwardPredictions(classes=np.array([0, 1]), proba=proba, baseline_proba=proba.copy(), fold_coefs=[], fold_meta=fold_meta)

    labels_a = pd.Series(rng.integers(0, 2, size=n), index=idx)
    continuous_return_a = pd.Series(rng.normal(size=n), index=idx)

    cutoff = 200  # inside fold 1's test window
    labels_b = labels_a.copy()
    continuous_return_b = continuous_return_a.copy()
    labels_b.iloc[cutoff:] = 1 - labels_b.iloc[cutoff:]
    continuous_return_b.iloc[cutoff:] = continuous_return_b.iloc[cutoff:] * 100 + 50

    result_a = causal_expected_value(wf_a, labels_a, continuous_return_a)
    result_b = causal_expected_value(wf_a, labels_b, continuous_return_b)

    pd.testing.assert_frame_equal(result_a.iloc[:cutoff], result_b.iloc[:cutoff])
