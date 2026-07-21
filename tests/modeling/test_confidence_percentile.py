"""
Tests for causal_confidence_percentile (mnq_system.modeling.evaluate) --
turns each horizon's raw, not-cross-comparable top-1 OOS confidence into a
percentile against that horizon's own prior OOS confidence history, so two
horizons whose label distributions have different base-rate skew (see
mnq_system.strategies.model_driven's finding that raw top-1 probability is
NOT comparable across horizons) can be compared on one scale.
"""

import numpy as np
import pandas as pd

from mnq_system.modeling.evaluate import (
    WalkForwardPredictions,
    causal_confidence_percentile,
    walk_forward_predict,
)


def _make_wf_from_confidences(top1_conf: np.ndarray, fold_sizes: list) -> WalkForwardPredictions:
    """Builds a minimal WalkForwardPredictions whose top-1 confidence at
    each row is exactly `top1_conf[row]` (values must be in [0.5, 1.0) so
    max(conf, 1-conf) == conf unambiguously), split into folds of the
    given sizes in order.
    """
    n = len(top1_conf)
    assert sum(fold_sizes) == n
    idx = pd.date_range("2020-01-01", periods=n, freq="5min", tz="UTC")
    proba = pd.DataFrame({0: 1 - top1_conf, 1: top1_conf}, index=idx)

    fold_meta = []
    pos = 0
    for i, size in enumerate(fold_sizes):
        test_index = idx[pos : pos + size]
        fold_meta.append({"fold": i, "test_index": test_index, "n_train": pos, "n_test": size})
        pos += size

    return WalkForwardPredictions(
        classes=np.array([0, 1]), proba=proba, baseline_proba=proba.copy(), fold_coefs=[], fold_meta=fold_meta
    )


def _make_signal_data(n=3000, seed=0):
    rng = np.random.default_rng(seed)
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    idx = pd.date_range("2020-01-01", periods=n, freq="1h", tz="UTC")
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)}, index=idx)
    labels = pd.Series(label, index=idx)
    return features, labels


def test_fold_0_rows_are_nan_no_prior_history_to_rank_against():
    rng = np.random.default_rng(0)
    top1_conf = rng.uniform(0.5, 1.0, size=300)
    wf = _make_wf_from_confidences(top1_conf, fold_sizes=[100, 100, 100])

    percentile = causal_confidence_percentile(wf)

    assert percentile.iloc[:100].isna().all()
    assert percentile.iloc[100:].notna().all()


def test_percentile_is_between_zero_and_one():
    rng = np.random.default_rng(1)
    top1_conf = rng.uniform(0.5, 1.0, size=400)
    wf = _make_wf_from_confidences(top1_conf, fold_sizes=[100, 100, 100, 100])

    percentile = causal_confidence_percentile(wf)

    defined = percentile.dropna()
    assert (defined >= 0).all() and (defined <= 1).all()


def test_within_fold_ranking_matches_raw_confidence_ranking():
    # Percentile-ranking must preserve the within-reference-distribution
    # order: a fold-2 row with higher raw confidence than another fold-2
    # row must get a higher (or equal) percentile.
    rng = np.random.default_rng(2)
    top1_conf = rng.uniform(0.5, 1.0, size=300)
    wf = _make_wf_from_confidences(top1_conf, fold_sizes=[100, 100, 100])

    percentile = causal_confidence_percentile(wf)

    fold2 = pd.DataFrame({"conf": top1_conf[100:200], "pct": percentile.iloc[100:200].to_numpy()})
    fold2_sorted = fold2.sort_values("conf")
    assert fold2_sorted["pct"].is_monotonic_increasing


def test_two_horizons_with_different_base_rates_become_comparable():
    # Horizon A's raw confidence is always higher than horizon B's (the
    # exact confound found in the model_driven investigation: a
    # more-class-imbalanced horizon structurally inflates raw top-1
    # probability) -- but both have the *same relative standing* within
    # their own history. After normalization the two percentile series
    # should closely track each other, even though raw confidence never
    # would.
    rng = np.random.default_rng(3)
    base = rng.uniform(0.0, 1.0, size=500)  # shared underlying "relative strength" driving both horizons
    conf_a = 0.5 + 0.45 * base  # horizon A: inflated, e.g. mean ~0.72
    conf_b = 0.5 + 0.10 * base  # horizon B: modest, e.g. mean ~0.52
    assert conf_a.mean() > conf_b.mean() + 0.15  # confirms the raw-confidence confound is present in this fixture

    fold_sizes = [100, 100, 100, 100, 100]
    wf_a = _make_wf_from_confidences(conf_a, fold_sizes)
    wf_b = _make_wf_from_confidences(conf_b, fold_sizes)

    pct_a = causal_confidence_percentile(wf_a).dropna()
    pct_b = causal_confidence_percentile(wf_b).dropna()

    # Raw confidence is not comparable (a is always higher) ...
    assert (conf_a[100:] > conf_b[100:]).all()
    # ... but the normalized percentiles are, on average, close to each other,
    # since `base` gives both horizons the same relative ranking structure.
    assert abs(pct_a.mean() - pct_b.mean()) < 0.05


def test_causality_changing_rows_strictly_after_a_cutoff_does_not_change_earlier_percentiles():
    rng = np.random.default_rng(4)
    top1_conf = rng.uniform(0.5, 1.0, size=400)
    fold_sizes = [100, 100, 100, 100]

    wf_a = _make_wf_from_confidences(top1_conf, fold_sizes)
    percentile_a = causal_confidence_percentile(wf_a)

    conf_b = top1_conf.copy()
    cutoff = 250  # inside the last fold
    conf_b[cutoff:] = 0.999  # wildly different tail
    wf_b = _make_wf_from_confidences(conf_b, fold_sizes)
    percentile_b = causal_confidence_percentile(wf_b)

    pd.testing.assert_series_equal(percentile_a.iloc[:cutoff], percentile_b.iloc[:cutoff])


def test_integrates_with_real_walk_forward_predict_output():
    features, labels = _make_signal_data()
    wf = walk_forward_predict(features, labels, n_folds=5, min_train_fraction=0.2)

    percentile = causal_confidence_percentile(wf)

    assert percentile.index.equals(wf.proba.index)
    assert percentile.notna().sum() > 0
    assert percentile.dropna().between(0, 1).all()
