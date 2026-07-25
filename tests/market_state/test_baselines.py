"""Baseline forecasts + the training-only selection rule."""
import numpy as np
import pytest

from market_state import baselines as B
from market_state import config as C
from market_state.labels import build_label_frame
from tests.market_state._synth import build_bars


@pytest.fixture(scope="module")
def frame():
    return build_label_frame(build_bars(n_days=6, k=0.001, pad=0.0002))


@pytest.fixture(scope="module")
def masks(frame):
    sample = frame["sample"].values
    days = np.array(sorted(set(frame["et_date"])))
    cutoff = days[4]
    train = sample & (frame["et_date"].values < cutoff)
    test = sample & (frame["et_date"].values >= cutoff)
    return train, test


def test_persistence_equals_rv_lag6(frame):
    pv = B.persistence_var(frame)
    assert pv.equals(frame["rv_lag6"])


def test_ewma_positive(frame):
    ev = B.ewma_var(frame)
    s = ev[frame["sample"].values]
    assert np.all(s.values > 0)


def test_har_recovers_constant(frame, masks):
    train, _ = masks
    k = 0.001
    log_pred = B.har_fit_predict(frame, train)
    var = np.exp(log_pred)[frame["sample"].values]
    assert np.allclose(var.values, C.HORIZON_BARS * k ** 2, rtol=1e-3)


def test_climatology_recovers_constant(frame, masks):
    train, _ = masks
    k = 0.001
    log_pred = B.climatology_fit_predict(frame, train)
    var = np.exp(log_pred)[frame["sample"].values]
    assert np.allclose(var.values, C.HORIZON_BARS * k ** 2, rtol=1e-3)


def test_select_baseline_returns_argmin(frame, masks):
    train, _ = masks
    res = B.select_baseline(frame, train)
    assert res["selected"] in C.CANDIDATE_BASELINES
    tq = res["train_qlike"]
    # selected must be the training-QLIKE argmin (never chosen using test data)
    assert np.isclose(tq[res["selected"]], min(tq.values()))
    assert all(np.isfinite(v) for v in tq.values())
