"""
FROZEN research configuration for Target A — Expected Realized Volatility (MES).

Every pre-registered numeric choice lives here; this is the machine-readable
mirror of docs/PRE_REGISTRATION_TARGET_A.md. Changing any value is equivalent
to terminating this study and registering a new one.

DELIBERATELY ABSENT (per §3 of the pre-registration): the prediction MODEL and
the concrete FEATURE list. Those are defined in docs/IMPLEMENTATION.md, approved
before development, and only then locked — they are not part of the frozen
scientific contract and live outside this file.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# ---------------------------------------------------------------------------
# Instrument & data boundaries (§6)
# ---------------------------------------------------------------------------
DEV_INSTRUMENT = "MES"                        # development only; no cross-market in Target A

DATA_START = pd.Timestamp("2019-05-01", tz="UTC")
DEV_END = pd.Timestamp("2024-12-31 23:59:59", tz="UTC")   # dev set ends here
HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")       # LOCKED — single final evaluation only
HOLDOUT_END = pd.Timestamp("2026-07-09", tz="UTC")

# ---------------------------------------------------------------------------
# Session (Eastern) & forecast eligibility window (§1)
# ---------------------------------------------------------------------------
TIMEZONE = "America/New_York"
BAR_MINUTES = 5
RTH_OPEN = (9, 30)
RTH_CLOSE = (16, 0)
FORECAST_START = (10, 0)     # first eligible forecast bar (OR fully formed)
FORECAST_LAST = (15, 25)     # last eligible forecast bar (full 6-bar fwd window ends by 16:00)

# ---------------------------------------------------------------------------
# Forecast horizon & realized-volatility label (§1, §2)
# ---------------------------------------------------------------------------
HORIZON_BARS = 6             # 30 minutes forward; SINGLE fixed horizon (no search)
# RV_t = sum_{i=1..HORIZON_BARS} r_{t+i}^2, r = ln(close/close_prev), intraday only.
# Model target = log(RV_t). Incomplete/gapped forward windows are excluded.

# ---------------------------------------------------------------------------
# ATR (used by the LMP diagnostic and the regime baselines)
# ---------------------------------------------------------------------------
ATR_PERIOD = 14

# ---------------------------------------------------------------------------
# Candidate baselines — FROZEN parameterizations (§4)
# ---------------------------------------------------------------------------
# (i) persistence : forecast forward variance = trailing HORIZON_BARS realized variance
# (ii) EWMA       : EWMA of 5-min squared returns (span below), scaled by HORIZON_BARS
# (iii) HAR-RV    : OLS on log-trailing-RV components at these look-backs (in bars);
#                   PRIOR_SESSION uses the prior RTH session's total realized variance
# (iv) time-of-day climatology : train mean log(RV) per HH:MM bucket
EWMA_SPAN = 20                                  # in 5-min bars
HAR_COMPONENT_BARS = (6, 24)                    # last 30 min, last ~2 hours
HAR_USE_PRIOR_SESSION = True                    # third HAR component = prior-session RV
CANDIDATE_BASELINES = ("persistence", "ewma", "har", "time_of_day")
# time-of-day is additionally a MANDATORY comparison the model must beat (§4).
MANDATORY_BASELINE = "time_of_day"

# ---------------------------------------------------------------------------
# Large-Move-Probability (LMP) diagnostic — FROZEN, not optimized (§10)
# ---------------------------------------------------------------------------
LMP_ATR_MULT = 2.0           # LMP event: max(H-ref, ref-L) over next HORIZON_BARS >= 2.0*ATR_t
# ref = close at the forecast bar; direction-agnostic. Diagnostic only (AUC + decile
# reliability of the model forecast as a score); does not by itself gate A.

# ---------------------------------------------------------------------------
# Walk-forward, purge, embargo (§5)
# ---------------------------------------------------------------------------
N_SPLITS = 6                 # 6 annual folds; fold 0 is training-only
EMBARGO_BARS = 12            # 2 x HORIZON_BARS
INNER_K = 5                  # inner purged folds for nested alpha-CV and OOF smearing

# ---------------------------------------------------------------------------
# v2 retransformation (Duan smearing) — FROZEN 2026-07-24 (PRE_REGISTRATION v2)
# ---------------------------------------------------------------------------
RETRANSFORM = "duan_smearing"   # log->variance mapping: h = s * exp(mu_hat)
SMEARING_OOF = True             # smearing factor from out-of-fold train residuals only
SMEARING_SCOPE = "all_qlike"    # applied everywhere a QLIKE decision is made (incl. alpha-selection)

# ---------------------------------------------------------------------------
# Statistics — day-level block bootstrap (§8, §9)
# ---------------------------------------------------------------------------
BOOTSTRAP_BLOCK_DAYS = 5
BOOTSTRAP_RESAMPLES = 5000
BOOTSTRAP_SEED = 20260724

# ---------------------------------------------------------------------------
# Pre-registered Go / No-Go (§8, §9, §13) — FROZEN
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GoNoGo:
    # practical-significance margin (§8) — FINALIZED
    min_qlike_reduction: float = 0.05          # >= 5.0% relative QLIKE reduction vs selected baseline
    # binding statistical gate (§8)
    max_prob_improvement_le_zero: float = 0.05  # block-bootstrap P(QLIKE improvement <= 0)
    require_positive_incremental_log_rv_r2: bool = True
    # calibration / unbiasedness (§9)
    mz_slope_lo: float = 0.90
    mz_slope_hi: float = 1.10
    # temporal stability (§9)
    min_years_positive: int = 4                 # of 6 dev years
    require_drop_best_year_positive: bool = True
    # mandatory seasonality control (§4)
    require_beats_time_of_day: bool = True
    # hold-out confirmation (§13)
    holdout_max_prob_improvement_le_zero: float = 0.05

GO_NO_GO = GoNoGo()

PREREG_SOURCE = {
    "frozen_on": "2026-07-24",
    "document": "market_state/docs/PRE_REGISTRATION_TARGET_A.md",
    "target": "A — Expected Realized Volatility",
}
