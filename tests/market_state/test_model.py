"""Ridge + nested-CV α selection: recovers signal, deterministic, no leakage."""
import numpy as np

from market_state import model as MODEL


def _synth(n=800, p=4, noise=0.3, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, p))
    beta = rng.normal(size=p)
    y = X @ beta * 0.5 + noise * rng.normal(size=n)
    rv = np.exp(y)                      # realized variance target (positive)
    entry = np.arange(n)
    exit_ = entry + 6
    return X, y, rv, entry, exit_


def test_select_alpha_returns_grid_member():
    X, y, rv, entry, exit_ = _synth()
    ntr = 600
    best, scores = MODEL.select_alpha(X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr])
    assert best in MODEL.ALPHA_GRID
    assert set(scores.keys()) == set(MODEL.ALPHA_GRID)


def test_fit_predict_recovers_signal():
    X, y, rv, entry, exit_ = _synth()
    ntr = 600
    mu, var, alpha, s_f, _ = MODEL.fit_predict(
        X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr], X[ntr:])
    # test-set log forecast should track the true log target well
    corr = np.corrcoef(mu, y[ntr:])[0, 1]
    assert corr > 0.8
    assert np.all(var > 0)
    assert s_f > 0


def test_deterministic():
    X, y, rv, entry, exit_ = _synth()
    ntr = 600
    a = MODEL.fit_predict(X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr], X[ntr:])
    b = MODEL.fit_predict(X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr], X[ntr:])
    assert a[2] == b[2]                              # same alpha
    assert a[3] == b[3]                              # same smearing factor
    assert np.allclose(a[0], b[0])                   # same predictions
