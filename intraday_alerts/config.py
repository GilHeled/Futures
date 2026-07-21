"""
FROZEN research configuration — every pre-registered choice lives here, and
nothing outside this file may introduce a researcher degree of freedom.
Changing any value here is equivalent to terminating the current experiment
and registering a new one (see docs/PRE_REGISTRATION.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# ---------------------------------------------------------------------------
# Instruments & data boundaries
# ---------------------------------------------------------------------------
DEV_INSTRUMENT = "MES"                       # development only
CROSS_MARKET = ("MNQ", "MYM", "M2K")         # pure out-of-sample replication

DATA_START = pd.Timestamp("2019-05-01", tz="UTC")
DEV_END = pd.Timestamp("2024-12-31 23:59:59", tz="UTC")      # dev set ends here
HOLDOUT_START = pd.Timestamp("2025-01-01", tz="UTC")          # LOCKED — untouched until Phase 1c
HOLDOUT_END = pd.Timestamp("2026-07-09", tz="UTC")

# ---------------------------------------------------------------------------
# Session (Eastern) — entries only after the opening range is fully formed
# ---------------------------------------------------------------------------
TIMEZONE = "America/New_York"
OPENING_RANGE = ((9, 30), (10, 0))    # 09:30–10:00 ET forms the OR feature
ENTRY_START = (10, 0)                 # first eligible entry 10:00 ET
ENTRY_END = (15, 0)                   # last eligible entry 15:00 ET
FLAT_BY = (15, 55)                    # force-flat 15:55 ET (inside Topstep's 3:10 PM CT cutoff)
BAR_MINUTES = 5

# ---------------------------------------------------------------------------
# Labeling / barriers (triple-barrier, ATR-scaled)
# ---------------------------------------------------------------------------
ATR_PERIOD = 14
PRIMARY_BARRIER = {"k": 1.0, "hold_bars": 6}   # k·ATR symmetric, 30-min max hold — THE candidate
ROBUSTNESS_BARRIERS = (                        # pre-registered robustness only (not selectable)
    {"k": 1.0, "hold_bars": 12},               # 60 min
    {"k": 1.5, "hold_bars": 6},
    {"k": 1.5, "hold_bars": 12},
)
N_BARRIER_TRIALS = 4                            # for the deflated-Sharpe correction

# ---------------------------------------------------------------------------
# Feature set (FIXED — no additions; every parameter pinned)
# ---------------------------------------------------------------------------
FEATURES = (
    "or_position",          # (close - OR_mid)/ATR
    "or_width",             # (OR_high - OR_low)/ATR
    "session_phase",        # fraction of RTH elapsed, 0..1
    "momentum_6",           # 6-bar (30-min) return / ATR
    "vwap_dev",             # (close - session_VWAP)/ATR
    "vol_regime",           # ATR / median(ATR over trailing 20 sessions)
    "overnight_gap",        # (RTH_open - prior_RTH_close)/ATR
    "return_since_open",    # (close - RTH_open)/ATR
    "participation",        # bar_volume / median(volume at same time-of-day, 20 sessions)
)
VOL_REGIME_LOOKBACK_SESSIONS = 20
PARTICIPATION_LOOKBACK_SESSIONS = 20
MOMENTUM_LOOKBACK_BARS = 6

# ---------------------------------------------------------------------------
# Model (simple-first; hyperparameters auto-selected in-fold, not by hand)
# ---------------------------------------------------------------------------
MODEL = "multinomial_logistic_l2"
C_GRID = (0.01, 0.1, 1.0, 10.0)      # selected via nested in-fold time-series CV
CLASS_WEIGHT = "balanced"
STANDARDIZE = True                    # scaler fit on train only

# ---------------------------------------------------------------------------
# Costs (net from the first result)
# ---------------------------------------------------------------------------
COMMISSION_PER_RT = 1.5               # $/contract round-turn (placeholder; Topstep commission schedule)
SPREAD_TICKS = 1.0                    # day-hours ~1 tick (supported by Corwin-Schultz estimate)
SLIPPAGE_TICKS = 1.0                  # each side

# ---------------------------------------------------------------------------
# Alert-selection policy (simulated INSIDE the backtest)
# ---------------------------------------------------------------------------
ONE_POSITION_AT_A_TIME = True
MAX_ENTRIES_PER_DAY = 3
COOLDOWN_BARS = 3                     # 15 min after any exit
# deterministic tie-break: higher EV; ties -> long before short; earliest bar first

# ---------------------------------------------------------------------------
# Topstep $50k — VERIFIED help.topstep.com, retrieved 2026-07-21
# (see docs/TOPSTEP_RULES_2026-07-21.md). Combine -> Express Funded rule set.
# ---------------------------------------------------------------------------
TOPSTEP_ACCOUNT_SIZE = 50_000
TOPSTEP_MAX_LOSS_LIMIT = 2_000        # trailing (EOD), locks at start; monitored intraday real-time
TOPSTEP_DAILY_LOSS_LIMIT = 1_000     # fixed for $50k; intraday flatten (forced break, not a violation)
TOPSTEP_CONTRACT_LIMIT_MICROS = 50   # 5 minis / 50 micros
RESEARCH_SIZE_MICROS = 1             # Phase 1 fixed sizing
SAFETY_BUFFER = 0.20                 # trade inside the real limits
EFFECTIVE_DAILY_STOP = TOPSTEP_DAILY_LOSS_LIMIT * (1 - SAFETY_BUFFER)   # $800
EFFECTIVE_MAX_LOSS = TOPSTEP_MAX_LOSS_LIMIT * (1 - SAFETY_BUFFER)       # $1600

# ---------------------------------------------------------------------------
# Statistics — day-level block bootstrap
# ---------------------------------------------------------------------------
BOOTSTRAP_BLOCK_DAYS = 5
BOOTSTRAP_RESAMPLES = 5000
BOOTSTRAP_SEED = 20260721

# ---------------------------------------------------------------------------
# Pre-registered numerical Go / No-Go (frozen)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GoNoGo:
    min_trades: int = 300
    min_expectancy_R: float = 0.03
    max_prob_mean_le_zero: float = 0.05     # Phase 1a / 1c
    deflated_sharpe_p_max: float = 0.05
    min_years_positive: int = 4             # of 6 dev years
    max_alerts_per_day: float = 3.0
    # timeout-term sensitivity gate (tret=0, before costs):
    timeout_min_retained_fraction: float = 0.50
    timeout_max_prob_mean_le_zero: float = 0.10
    # Phase 1b cross-market:
    xmarket_min_pass: int = 2               # of 3
    xmarket_max_prob_mean_le_zero: float = 0.10

GO_NO_GO = GoNoGo()

TOPSTEP_SOURCE = {
    "verified_on": "2026-07-21",
    "urls": (
        "https://help.topstep.com/en/articles/8284197-trading-combine-parameters",
        "https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit",
        "https://help.topstep.com/en/articles/10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account",
    ),
}
