"""
FROZEN Study-1 configuration (mirror of docs/PRE_REGISTRATION_STUDY1_STOP_TARGET.md).
No value here may change without a new pre-registration.
"""
from __future__ import annotations

from market_state import config as MS   # reuse locked dev/hold-out boundaries, session

INSTRUMENT = "MES"

# --- session / eligibility (§3, §5) — inherit the frozen market-state window ---
TIMEZONE = MS.TIMEZONE
FORECAST_START = MS.FORECAST_START     # (10, 0)  first eligible entry bar
FORECAST_LAST = MS.FORECAST_LAST       # (15, 25) last eligible entry bar
FLAT_BY = (15, 55)                     # force-flat baseline exit

# --- base strategies (§5) — generic, parameter-light, fixed a priori ---
EMA_FAST = 9
EMA_SLOW = 21
VWAP_ATR_MULT = 1.5                    # VWAP-fade entry band (ATR used for ENTRY only)
ATR_PERIOD = 14
STRATEGIES = ("ema_cross", "vwap_fade")

# --- volatility sources (§2) ---
VOL_SOURCES = ("none", "naive", "forecast")   # none=const, naive=HAR, forecast=frozen v2

# --- stop/target configs (§6): (name, k stop-mult, m target-mult) ---
KM_CONFIGS = (("C1", 1.0, 1.0), ("C2", 1.0, 2.0), ("C3", 1.5, 1.0))
N_CONFIGS = len(KM_CONFIGS) * len(STRATEGIES)   # 6 forecast-arm configs

# --- execution / costs (§7) — existing MES cost model ---
POINT_VALUE = 5.0        # $ per index point (MES)
TICK = 0.25              # index points per tick ($1.25)
COMMISSION_RT = 1.5      # $ per round-turn contract
SPREAD_TICKS = 1.0       # round-turn spread, ticks
SLIP_TICKS_PER_SIDE = 1.0
SIZE = 1.0               # fixed 1-contract exposure (fractional allowed; sizing = Study 2)

# --- inference (§9) ---
BOOTSTRAP_BLOCK_DAYS = 5
BOOTSTRAP_RESAMPLES = 5000
BOOTSTRAP_SEED = 20260724
TRADING_DAYS_PER_YEAR = 252

# --- Go / No-Go (§11) ---
ALPHA = 0.05
ALPHA_CORRECTED = ALPHA / N_CONFIGS          # 0.05/6 Bonferroni, per-config primary
DD_TOLERANCE = 0.10                          # forecast maxDD not >10% (relative) worse than naive
