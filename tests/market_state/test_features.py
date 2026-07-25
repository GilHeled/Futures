"""Concrete-feature sanity: columns present, valid ranges, direction-free quantities."""
import numpy as np
import pytest

from market_state.features import FEATURES, compute_features
from tests.market_state._synth import build_bars


@pytest.fixture(scope="module")
def feats():
    return compute_features(build_bars(n_days=8, k=0.001, pad=0.0004))


def test_all_ten_features_present(feats):
    assert list(feats.columns) == list(FEATURES)
    assert len(FEATURES) == 10


def test_session_phase_in_unit_interval(feats):
    sp = feats["session_phase"].dropna()
    assert sp.between(0.0, 1.0).all()


def test_efficiency_ratio_in_unit_interval(feats):
    er = feats["efficiency_ratio"].dropna()
    assert er.between(0.0, 1.0 + 1e-9).all()


def test_gap_abs_nonnegative(feats):
    ga = feats["gap_abs"].dropna()
    assert (ga >= 0).all()


def test_efficiency_ratio_is_one_on_monotone_path(feats):
    # constant positive drift => net move == gross move (telescoping) => ER == 1
    er = feats["efficiency_ratio"].dropna()
    assert np.allclose(er.values, 1.0, atol=1e-6)
