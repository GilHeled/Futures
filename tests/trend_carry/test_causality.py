"""Causality guarantees for the continuous-returns layer — WRITTEN FIRST.

These encode the overnight-line lesson: verify feature/data causality before any
signal or statistic is computed. Two guarantees:

  1. Roll-gap correction: on a roll day the return uses the incoming contract's
     own prior price (rank 1 at t-1), never the fake gap between two different
     contracts' prices.
  2. Prefix stability: recomputing returns / the adjusted index on a series
     truncated at any T reproduces every value for t <= T exactly. This is the
     operational definition of "no look-ahead."
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trend_carry import config as C
from trend_carry import continuous as K
from trend_carry import data as D


def _synthetic():
    """Front rolls from contract A(100) to B(200) on day 3; rank 1 holds the
    next contract each day (B while front=A, then C)."""
    dates = pd.date_range("2020-01-01", periods=5, freq="D", tz="UTC")
    v0 = pd.DataFrame(
        {"close": [100.0, 110.0, 121.0, 200.0, 220.0],
         "instrument_id": [100, 100, 100, 200, 200]},
        index=dates)
    v1 = pd.DataFrame(
        {"close": [180.0, 190.0, 195.0, 300.0, 330.0],
         "instrument_id": [200, 200, 200, 300, 300]},
        index=dates)
    return v0, v1


def test_roll_gap_corrected_via_rank1():
    v0, v1 = _synthetic()
    r = K.causal_return_series(v0, v1)
    # non-roll days: plain front returns
    assert r.iloc[1] == pytest.approx(0.10)          # 110/100
    assert r.iloc[2] == pytest.approx(121 / 110 - 1)
    assert r.iloc[4] == pytest.approx(0.10)          # 220/200
    # roll day 3: uses v1_2 (=195, the incoming contract B), NOT v0_2 (=121)
    assert r.iloc[3] == pytest.approx(200 / 195 - 1)
    assert abs(r.iloc[3] - (200 / 121 - 1)) > 0.1    # the fake gap is avoided


def test_returns_prefix_stable():
    v0, v1 = _synthetic()
    full = K.causal_return_series(v0, v1)
    for cut in range(2, 5):
        r_trunc = K.causal_return_series(v0.iloc[:cut], v1.iloc[:cut])
        # every recomputed value matches the full-series value on the overlap
        pd.testing.assert_series_equal(
            r_trunc, full.iloc[:cut], check_names=False)


def test_adjusted_index_prefix_stable():
    v0, v1 = _synthetic()
    r_full = K.causal_return_series(v0, v1)
    idx_full = K.ratio_adjusted_index(r_full)
    for cut in range(2, 5):
        r_t = K.causal_return_series(v0.iloc[:cut], v1.iloc[:cut])
        idx_t = K.ratio_adjusted_index(r_t)
        # levels are relative; compare pairwise ratios (scale/anchor invariant)
        common = idx_t.dropna()
        ref = idx_full.loc[common.index]
        rel = (common / common.iloc[0]) / (ref / ref.iloc[0])
        assert np.allclose(rel.values, 1.0, atol=1e-12)


# --------------------------------------------------------------------------- #
# Integration on the real cache (skips cleanly if the pull hasn't run yet)    #
# --------------------------------------------------------------------------- #
def _have_cache(root="ES") -> bool:
    return D._parquet(root).exists()


@pytest.mark.skipif(not _have_cache(), reason="universe not pulled yet")
def test_real_returns_prefix_stable():
    roots = ["ES", "CL", "6E"]  # sample across sectors
    panels = {r: D.load_parent(r) for r in roots}
    rolls = {r: K.build_roll(panels[r]) for r in roots}
    full = K.build_returns(rolls, roots=roots)
    for T in ["2015-06-30", "2019-12-31", "2023-01-15"]:
        Tts = pd.Timestamp(T, tz="UTC")
        # truncate the RAW contract panel, rebuild roll+returns from scratch
        rolls_t = {r: K.build_roll(panels[r][panels[r].index <= Tts]) for r in roots}
        part = K.build_returns(rolls_t, roots=roots)
        overlap = full.index[full.index <= Tts]
        a = full.loc[overlap, roots].fillna(0).values
        b = part.reindex(overlap)[roots].fillna(0).values
        assert np.allclose(a, b, atol=1e-12), f"prefix instability at {T}"


@pytest.mark.skipif(not _have_cache(), reason="universe not pulled yet")
def test_real_returns_sane():
    r = K.build_returns()
    assert set(r.columns) == set(C.ROOTS)
    frac_extreme = (r.abs() > 0.25).sum().sum() / r.notna().sum().sum()
    assert frac_extreme < 0.001, f"too many extreme daily returns: {frac_extreme:.4%}"
