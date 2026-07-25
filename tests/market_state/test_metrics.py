"""Metric correctness on hand-checkable inputs."""
import numpy as np

from market_state import metrics as M


def test_qlike_zero_on_perfect_forecast():
    rv = np.array([1.0, 2.0, 3.0, 0.5])
    assert M.qlike(rv, rv) == 0.0


def test_qlike_known_value():
    # single obs rv=2, h=1 => 2 - ln2 - 1 = 1 - ln2
    assert np.isclose(M.qlike([2.0], [1.0]), 1.0 - np.log(2.0))


def test_qlike_contributions_mean_equals_qlike():
    rv = np.array([1.0, 2.0, 3.0, 0.7])
    h = np.array([1.2, 1.5, 2.0, 0.9])
    contribs = M.qlike_contributions(rv, h)
    assert np.isclose(np.nanmean(contribs), M.qlike(rv, h))


def test_qlike_reduction():
    assert np.isclose(M.qlike_reduction(1.0, 0.5), 0.5)


def test_r2_perfect_and_incremental():
    y = np.array([0.1, 0.5, -0.3, 1.2, 0.0])
    assert np.isclose(M.r2_vs_mean(y, y), 1.0)
    assert np.isclose(M.incremental_r2(y, y, y + 0.5), 1.0)   # model perfect
    base = y + 0.3
    assert np.isclose(M.incremental_r2(y, base, base), 0.0)   # model == baseline


def test_mincer_zarnowitz_recovers_linear():
    f = np.array([0.2, 0.4, 0.6, 0.8, 1.0])
    realized = 2.0 * f + 0.1
    mz = M.mincer_zarnowitz(realized, f)
    assert np.isclose(mz["slope"], 2.0)
    assert np.isclose(mz["intercept"], 0.1)
    assert np.isclose(mz["r2"], 1.0)


def test_auc_separable():
    score = np.array([0.1, 0.2, 0.3, 0.9, 0.8, 0.7])
    event = np.array([0, 0, 0, 1, 1, 1])
    assert M.auc(score, event) == 1.0
    assert M.auc(-score, event) == 0.0


def test_auc_ties_half():
    score = np.array([0.5, 0.5, 0.5, 0.5])
    event = np.array([0, 1, 0, 1])
    assert np.isclose(M.auc(score, event), 0.5)


def test_decile_reliability_monotone():
    rng = np.random.default_rng(0)
    score = np.linspace(0, 1, 500)
    outcome = (rng.random(500) < score).astype(float)   # P(event) rises with score
    rel = M.decile_reliability(score, outcome, n_bins=5)
    assert rel["mean_outcome"].is_monotonic_increasing
