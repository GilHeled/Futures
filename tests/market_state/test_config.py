"""Frozen-config sanity checks — guard against accidental drift of pre-registered values."""
from market_state import config as C


def test_frozen_scalars():
    assert C.HORIZON_BARS == 6                      # 30-min horizon (§1)
    assert C.LMP_ATR_MULT == 2.0                    # frozen LMP threshold (§10)
    assert C.EMBARGO_BARS == 2 * C.HORIZON_BARS     # embargo = 2x horizon (§5)
    assert C.N_SPLITS == 6                           # 6 annual folds (§5)
    assert C.FORECAST_START == (10, 0)
    assert C.FORECAST_LAST == (15, 25)


def test_frozen_go_no_go():
    g = C.GO_NO_GO
    assert g.min_qlike_reduction == 0.05            # finalized practical margin (§8)
    assert g.max_prob_improvement_le_zero == 0.05   # statistical gate (§8)
    assert g.mz_slope_lo == 0.90 and g.mz_slope_hi == 1.10  # calibration (§9)
    assert g.min_years_positive == 4                # temporal stability (§9)
    assert g.require_drop_best_year_positive
    assert g.require_beats_time_of_day             # mandatory seasonality control (§4)


def test_boundaries_ordered():
    assert C.DATA_START < C.DEV_END < C.HOLDOUT_START < C.HOLDOUT_END


def test_baseline_registry():
    assert C.MANDATORY_BASELINE in C.CANDIDATE_BASELINES
    assert set(C.CANDIDATE_BASELINES) == {"persistence", "ewma", "har", "time_of_day"}
