"""
Tests for the confidence-decile ranking analysis (mnq_system.modeling.evaluate):
"market state -> better-than-random prediction" is a different claim from
"higher model confidence -> better decisions" -- these tests prove the
harness can actually detect the second when it's really there, and does
not manufacture it when confidence carries no decision-relevant information.
"""

import numpy as np
import pandas as pd

from mnq_system.modeling.evaluate import (
    default_class_direction,
    evaluate_horizon,
    _bootstrap_decile_spread,
    _confidence_decile_raw,
    _confidence_decile_table,
    _directional_confidence_decile_raw,
    _monotonicity_correlation,
)


def test_default_class_direction_five_bins_matches_labels_convention():
    classes = np.array([0, 1, 2, 3, 4])  # big_down, small_down, flat, small_up, big_up

    direction = default_class_direction(classes)

    assert direction == {0: -1, 1: -1, 2: 0, 3: 1, 4: 1}


def test_default_class_direction_binary_has_no_flat_class():
    classes = np.array([0, 1])

    direction = default_class_direction(classes)

    assert direction == {0: -1, 1: 1}


def test_confidence_decile_table_top_decile_has_highest_accuracy_when_confidence_is_informative():
    # Construct predictions where confidence and correctness are genuinely
    # linked: as confidence rises from 0.5 to 1.0, so does the chance the
    # top-1 prediction is actually right.
    rng = np.random.default_rng(0)
    n = 5000
    confidence = rng.uniform(0.5, 1.0, size=n)
    is_correct = rng.uniform(0, 1, size=n) < confidence
    y_true = np.where(is_correct, 1, 0)
    proba = np.column_stack([1 - confidence, confidence])
    classes = np.array([0, 1])

    raw = _confidence_decile_raw(y_true, proba, classes)
    table = _confidence_decile_table(raw)

    assert not table.empty
    # Monotonic: last decile's accuracy meaningfully exceeds the first's.
    assert table.iloc[-1]["accuracy"] > table.iloc[0]["accuracy"] + 0.1


def test_confidence_decile_spread_is_significant_for_a_genuine_relationship():
    rng = np.random.default_rng(1)
    n = 5000
    confidence = rng.uniform(0.5, 1.0, size=n)
    is_correct = rng.uniform(0, 1, size=n) < confidence
    y_true = np.where(is_correct, 1, 0)
    proba = np.column_stack([1 - confidence, confidence])
    classes = np.array([0, 1])

    raw = _confidence_decile_raw(y_true, proba, classes)
    spread = _bootstrap_decile_spread(raw, "correct")
    mono = _monotonicity_correlation(raw, "correct")

    assert spread["ci_low"] > 0  # top decile clearly beats bottom decile
    assert mono["spearman_r"] > 0.1
    assert mono["p_value"] < 0.01


def test_confidence_decile_spread_is_not_significant_when_confidence_is_uninformative():
    # Confidence values are randomly shuffled relative to correctness --
    # any apparent "decile ranking" here would be a false positive.
    rng = np.random.default_rng(2)
    n = 5000
    confidence = rng.uniform(0.5, 1.0, size=n)
    y_true = rng.integers(0, 2, size=n)  # independent of confidence
    proba = np.column_stack([1 - confidence, confidence])
    classes = np.array([0, 1])

    raw = _confidence_decile_raw(y_true, proba, classes)
    spread = _bootstrap_decile_spread(raw, "correct")

    assert spread["ci_low"] <= 0  # must not claim a confident ranking out of noise


def test_confidence_decile_raw_computes_signed_return_in_predicted_direction():
    y_true = np.array([4, 4, 0, 0])  # big_up, big_up, big_down, big_down
    # Model's top-1 prediction matches for rows 0 and 2, wrong for 1 and 3.
    proba = np.array(
        [
            [0.05, 0.05, 0.1, 0.1, 0.7],  # predicts big_up (class 4)
            [0.05, 0.05, 0.1, 0.1, 0.7],  # predicts big_up (class 4), true label is big_up too
            [0.7, 0.1, 0.1, 0.05, 0.05],  # predicts big_down (class 0)
            [0.05, 0.1, 0.1, 0.05, 0.7],  # predicts big_up (class 4), true label is big_down
        ]
    )
    classes = np.array([0, 1, 2, 3, 4])
    continuous_return = np.array([2.0, -1.0, -3.0, -3.0])  # actual ATR-normalized forward returns

    raw = _confidence_decile_raw(y_true, proba, classes, continuous_return=continuous_return)

    # direction for predicted class 4 (big_up) is +1, class 0 (big_down) is -1
    expected_signed_return = np.array([2.0 * 1, -1.0 * 1, -3.0 * -1, -3.0 * 1])
    np.testing.assert_allclose(raw["signed_return"].to_numpy(), expected_signed_return)


def test_evaluate_horizon_computes_confidence_deciles_when_continuous_returns_supplied():
    rng = np.random.default_rng(3)
    n = 3000
    signal = rng.normal(size=n)
    label = (signal + rng.normal(scale=0.3, size=n) > 0).astype(int)
    features = pd.DataFrame({"signal": signal, "noise": rng.normal(size=n)})
    labels = pd.Series(label)
    continuous_returns = pd.Series(signal + rng.normal(scale=0.3, size=n))  # correlated with the label/signal

    result = evaluate_horizon(features, labels, horizon=1, n_folds=5, continuous_returns=continuous_returns)

    assert not result.confidence_deciles.empty
    assert "signed_return" in result.confidence_deciles.columns
    assert not np.isnan(result.return_monotonicity["spearman_r"])


def test_evaluate_horizon_confidence_deciles_empty_without_continuous_returns():
    rng = np.random.default_rng(4)
    n = 500
    features = pd.DataFrame({"x": rng.normal(size=n)})
    labels = pd.Series(rng.integers(0, 2, size=n))

    result = evaluate_horizon(features, labels, horizon=1, n_folds=4)

    assert "signed_return" not in result.confidence_deciles.columns


def test_directional_filter_recovers_a_real_relationship_diluted_by_flat_predictions():
    # Regression scenario for a real bug found on live data: at a short
    # horizon the model predicts "flat" (direction 0) for ~95% of rows
    # regardless of confidence, which contributes exactly 0 to signed_return
    # and swamps a real relationship living in the small directional
    # minority -- the all-rows metric looks noisy/flat, but restricting to
    # directional-only rows must recover the genuine monotonic relationship.
    rng = np.random.default_rng(5)
    n = 20000
    is_directional = rng.uniform(0, 1, size=n) < 0.05  # 5% of rows get an actual directional call
    # In both branches, `confidence` is the probability mass on whichever
    # class actually wins argmax for that row (flat for the non-directional
    # majority, up/down for the directional minority) -- always > 0.5 so it
    # really is the top-1 probability.
    confidence = rng.uniform(0.5, 1.0, size=n)

    y_true = np.full(n, 2)  # mostly "flat" is the true label too
    top1_is_up = rng.uniform(0, 1, size=n) < 0.5
    # Within the directional minority, confidence genuinely predicts payoff:
    # E[signed_return | directional] = confidence + noise (a real, positive,
    # monotonically increasing relationship), by construction.
    direction_sign = np.where(top1_is_up, 1.0, -1.0)
    continuous_return = rng.normal(scale=0.3, size=n)
    continuous_return[is_directional] = (
        direction_sign[is_directional] * confidence[is_directional] + rng.normal(scale=0.3, size=is_directional.sum())
    )

    classes = np.array([0, 1, 2, 3, 4])
    proba = np.zeros((n, 5))
    for i in range(n):
        if is_directional[i]:
            col = 4 if top1_is_up[i] else 0
            proba[i, col] = confidence[i]
            proba[i, 2] = 1 - confidence[i]
        else:
            proba[i, 2] = confidence[i]
            proba[i, 0 if top1_is_up[i] else 4] = 1 - confidence[i]

    raw = _confidence_decile_raw(y_true, proba, classes, continuous_return=continuous_return)
    directional_raw = _directional_confidence_decile_raw(raw)

    all_spread = _bootstrap_decile_spread(raw, "signed_return")
    directional_spread = _bootstrap_decile_spread(directional_raw, "signed_return")

    assert len(directional_raw) < len(raw) * 0.10  # confirms the minority-dilution setup
    assert directional_spread["ci_low"] > 0  # the real relationship is recovered
    # The all-rows metric should be much weaker/noisier than the directional-only one.
    assert directional_spread["mean_diff"] > abs(all_spread["mean_diff"])
