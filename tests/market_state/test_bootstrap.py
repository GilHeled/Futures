"""Day-level block bootstrap behavior + paired-improvement aggregation."""
import numpy as np

from market_state.bootstrap import block_bootstrap_mean, daily_paired_improvement


def test_positive_array_never_le_zero():
    res = block_bootstrap_mean(np.ones(60), resamples=1000, seed=1)
    assert res["mean"] == 1.0
    assert res["p_mean_le_zero"] == 0.0
    assert res["ci_lo"] == 1.0 and res["ci_hi"] == 1.0


def test_negative_array_always_le_zero():
    res = block_bootstrap_mean(-np.ones(60), resamples=1000, seed=1)
    assert res["p_mean_le_zero"] == 1.0


def test_deterministic_with_seed():
    x = np.random.default_rng(7).normal(0.3, 1.0, size=80)
    a = block_bootstrap_mean(x, seed=123, resamples=2000)
    b = block_bootstrap_mean(x, seed=123, resamples=2000)
    assert a["p_mean_le_zero"] == b["p_mean_le_zero"]
    assert a["ci_lo"] == b["ci_lo"] and a["ci_hi"] == b["ci_hi"]


def test_ci_brackets_mean():
    x = np.random.default_rng(3).normal(0.5, 1.0, size=120)
    res = block_bootstrap_mean(x, seed=42)
    assert res["ci_lo"] <= res["mean"] <= res["ci_hi"]


def test_daily_paired_improvement():
    dates = ["d1", "d1", "d2", "d2"]
    base = [1.0, 3.0, 2.0, 2.0]     # daily means: d1=2, d2=2
    model = [0.0, 2.0, 1.0, 3.0]    # daily means: d1=1, d2=2
    days, imp = daily_paired_improvement(dates, base, model)
    assert list(days) == ["d1", "d2"]
    assert np.allclose(imp, [1.0, 0.0])
