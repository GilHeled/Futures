"""Label-builder correctness: forward RV, no cross-session leakage, sample mask, LMP."""
import numpy as np
import pytest

from market_state import config as C
from market_state.labels import build_label_frame
from tests.market_state._synth import build_bars


@pytest.fixture(scope="module")
def frame():
    # constant within-session log return k => RV over 6 bars = 6*k^2 exactly
    # (pad only sets high/low, so ATR>0 and the LMP event is exercised; closes
    # — and therefore RV — are unaffected by pad)
    return build_label_frame(build_bars(n_days=4, k=0.001, pad=0.0002))


def test_forward_rv_exact_on_constant_path(frame):
    k = 0.001
    expected_rv = C.HORIZON_BARS * k ** 2
    interior = frame[frame["rv"].notna()]
    assert np.allclose(interior["rv"].values, expected_rv, rtol=1e-6, atol=1e-12)
    assert np.allclose(interior["log_rv"].values, np.log(expected_rv), atol=1e-6)


def test_trailing_rv_lag6_exact(frame):
    k = 0.001
    valid = frame[frame["rv_lag6"].notna()]
    assert np.allclose(valid["rv_lag6"].values, C.HORIZON_BARS * k ** 2, rtol=1e-6, atol=1e-12)


def test_no_forward_window_past_session_close(frame):
    # within each session, the last 6 bars (15:30..15:55) must have NaN forward RV
    for _, day in frame.groupby("et_date"):
        day = day.sort_values("pos")
        assert day["rv"].iloc[-6:].isna().all()
        # the 15:25 bar (7th from last) is the last with a complete window
        assert not np.isnan(day["rv"].iloc[-7])


def test_sample_mask_excludes_first_session(frame):
    counts = frame.groupby("et_date")["sample"].sum()
    days = list(counts.index)
    # day 1 has no prior session => rv_prev_session missing => zero samples
    assert counts.iloc[0] == 0
    # later days: eligible 10:00–15:25 (66) intersected with rv_lag24 (j>=24) => 48
    assert (counts.iloc[1:] == 48).all()


def test_lmp_event_matches_threshold(frame):
    fin = frame[frame["lmp_event"].notna()]
    lhs = fin["lmp_event"].values.astype(bool)
    rhs = (fin["lmp_excursion_atr"].values >= C.LMP_ATR_MULT)
    assert np.array_equal(lhs, rhs)


def test_lmp_event_fires_on_large_forward_move():
    # inject a large upward spike; a forecast bar whose window contains it should fire
    bars = build_bars(n_days=2, k=0.0, pad=0.0002)
    bars = bars.copy()
    # pick a bar mid-session on day 2 and spike the high 6 bars ahead
    day2 = bars[bars["et_date"] == sorted(set(bars["et_date"]))[1]]
    ref_ts = day2.index[40]
    spike_ts = day2.index[44]
    bars.loc[spike_ts, "high"] = bars.loc[ref_ts, "close"] * 1.5   # enormous excursion
    frame = build_label_frame(bars)
    assert frame.loc[ref_ts, "lmp_event"] == 1.0


def test_exit_pos_is_horizon_ahead(frame):
    assert (frame["exit_pos"] - frame["pos"] == C.HORIZON_BARS).all()
