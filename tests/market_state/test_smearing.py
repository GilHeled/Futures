"""v2 Duan smearing retransformation: corrects the log→variance level bias,
leakage-safe (OOF), and leaves μ̂-based quantities untouched."""
import numpy as np

from market_state import config as C
from market_state import model as MODEL


def _lognormal_dgp(n=1600, p=4, sig2=0.5, seed=1):
    """y = log(RV) with conditional mean X@beta and homoskedastic noise var sig2.
    Then E[RV|X] = exp(mu + sig2/2), so naive exp(mû) is biased LOW by exp(sig2/2)."""
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, p))
    beta = rng.normal(size=p)
    mu_true = X @ beta * 0.5
    y = mu_true + rng.normal(0.0, np.sqrt(sig2), size=n)
    rv = np.exp(y)
    entry = np.arange(n)
    exit_ = entry + 6
    return X, y, rv, entry, exit_


def test_smearing_corrects_level_bias():
    X, y, rv, entry, exit_ = _lognormal_dgp()
    ntr = 1100
    mu, var, alpha, s_f, _ = MODEL.fit_predict(
        X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr], X[ntr:])
    rv_test = rv[ntr:]
    naive = np.exp(mu)                                  # what v1 used (biased low)
    naive_ratio = naive.mean() / rv_test.mean()
    smeared_ratio = var.mean() / rv_test.mean()
    assert naive_ratio < 0.9                            # naive under-forecasts the level
    assert 0.85 <= smeared_ratio <= 1.2                # smearing restores the level
    assert s_f > 1.0                                    # exp(positive residual variance)


def test_smearing_factor_is_the_oof_quantity_not_insample():
    # s_f is EXACTLY the out-of-fold residual smearing factor (leakage-safe def),
    # and is NOT the in-sample fitted-residual smearing factor.
    X, y, rv, entry, exit_ = _lognormal_dgp(sig2=0.6, seed=3)
    ntr = 1100
    s_oof = MODEL.smearing_factor(X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr], alpha=1.0)
    mu, yv, _ = MODEL._inner_oof(
        X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr], 1.0, MODEL.INNER_K, C.EMBARGO_BARS)
    assert np.isclose(s_oof, float(np.mean(np.exp(yv - mu))))     # exact OOF quantity
    scaler, ridge = MODEL._fit_scaler_ridge(X[:ntr], y[:ntr], 1.0)
    s_insample = float(np.mean(np.exp(y[:ntr] - ridge.predict(scaler.transform(X[:ntr])))))
    assert not np.isclose(s_oof, s_insample)                      # NOT the in-sample factor


def test_lmp_score_is_mu_hat_invariant_to_smearing():
    # μ̂ (LMP score) is unchanged by the smearing factor; only the variance forecast scales
    X, y, rv, entry, exit_ = _lognormal_dgp(seed=5)
    ntr = 1100
    mu, var, alpha, s_f, _ = MODEL.fit_predict(
        X[:ntr], y[:ntr], rv[:ntr], entry[:ntr], exit_[:ntr], X[ntr:])
    assert np.allclose(var, s_f * np.exp(mu))           # var = s_f · exp(μ̂), μ̂ carries the ranking
