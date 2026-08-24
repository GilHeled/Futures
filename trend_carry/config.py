"""FROZEN configuration for the trend + carry program.

Every value here is fixed by the pre-registration (docs/PRE_REGISTRATION.md) and
must not be changed to improve results. Changes are permitted only to correct an
implementation bug or a protocol violation, and must be documented.
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Universe (14 roots, 6 sectors) — frozen §12.1                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Contract:
    root: str
    sector: str
    tick_size: float      # price increment in the series' quote units
    point_value: float    # USD per 1.0 of price
    micro: str | None     # micro proxy for deployment (research uses the root)

    @property
    def tick_value(self) -> float:
        return self.tick_size * self.point_value


# tick_size / point_value are CME contract specs in the units Databento reports.
UNIVERSE: tuple[Contract, ...] = (
    # Equities
    Contract("ES", "equities", 0.25, 50.0, "MES"),
    Contract("NQ", "equities", 0.25, 20.0, "MNQ"),
    # Rates
    Contract("ZT", "rates", 0.0078125, 2000.0, None),   # 2y note, 1/128 tick = $15.625
    Contract("ZN", "rates", 0.015625, 1000.0, None),    # 10y note, 1/64 tick = $15.625
    Contract("ZB", "rates", 0.03125, 1000.0, None),     # 30y bond, 1/32 tick = $31.25
    # FX
    Contract("6E", "fx", 0.00005, 125000.0, "M6E"),     # EUR, tick $6.25
    Contract("6J", "fx", 0.0000005, 12500000.0, None),  # JPY, tick $6.25
    Contract("6A", "fx", 0.00005, 100000.0, "M6A"),     # AUD, tick $5.00
    # Metals
    Contract("GC", "metals", 0.10, 100.0, "MGC"),       # Gold, tick $10
    Contract("HG", "metals", 0.0005, 25000.0, "MHG"),   # Copper, tick $12.50
    # Energy
    Contract("CL", "energy", 0.01, 1000.0, "MCL"),      # Crude, tick $10
    Contract("NG", "energy", 0.001, 10000.0, None),     # Nat gas, tick $10
    # Grains (quoted in cents/bushel)
    Contract("ZC", "grains", 0.25, 50.0, None),         # Corn, tick $12.50
    Contract("ZS", "grains", 0.25, 50.0, None),         # Soybeans, tick $12.50
)

ROOTS: tuple[str, ...] = tuple(c.root for c in UNIVERSE)
SECTORS: tuple[str, ...] = tuple(dict.fromkeys(c.sector for c in UNIVERSE))
BY_ROOT: dict[str, Contract] = {c.root: c for c in UNIVERSE}

# --------------------------------------------------------------------------- #
# Data — frozen §3, §12.2                                                     #
# --------------------------------------------------------------------------- #
DATASET = "GLBX.MDP3"
SCHEMA_DAILY = "ohlcv-1d"
ROLL_RULE = "v"          # volume roll (Databento continuous stype: PARENT.v.N)
RANKS = (0, 1)           # front (.v.0) and next (.v.1); Phase 1 uses rank 0 only
DATA_START = "2010-06-06"
DATA_END = "2026-07-09"  # == locked hold-out end

# --------------------------------------------------------------------------- #
# Splits — frozen §6                                                          #
# --------------------------------------------------------------------------- #
DEV_START = "2010-06-06"
DEV_END = "2019-12-31"
OOS_START = "2020-01-01"
OOS_END = "2024-12-31"
HOLDOUT_START = "2025-01-01"
HOLDOUT_END = "2026-07-09"   # LOCKED — touched once, only if OOS passes

# --------------------------------------------------------------------------- #
# Signals — frozen §4, §12.5                                                  #
# --------------------------------------------------------------------------- #
TREND_LOOKBACKS = (21, 63, 126, 252)   # trading days; equal-weight sign ensemble
VOL_LOOKBACK = 60                      # trading days, causal realized-vol window

# --------------------------------------------------------------------------- #
# Portfolio — frozen §5, §12.7                                                #
# --------------------------------------------------------------------------- #
VOL_TARGET_ANNUAL = 0.10   # scaling only; net Sharpe is invariant to this
SLEEVE_SPLIT = {"trend": 0.5, "carry": 0.5}   # Phase 2
TRADING_DAYS = 252

# --------------------------------------------------------------------------- #
# Costs — frozen §8, §12.8                                                    #
# --------------------------------------------------------------------------- #
COMMISSION_PER_SIDE = 2.50        # USD per contract per side
IMPACT_TICKS_PER_SIDE = 2.0       # 1 tick spread + 1 tick slippage
COST_STRESS_MULT = 2.0            # Go/No-Go requires survival at 2x baseline

# --------------------------------------------------------------------------- #
# Go / No-Go — frozen §8, §12.9                                               #
# --------------------------------------------------------------------------- #
SHARPE_MIN = 0.5
BOOTSTRAP_P_MAX = 0.05
BOOTSTRAP_BLOCK_DAYS = 21
BOOTSTRAP_RESAMPLES = 5000
BOOTSTRAP_SEED = 20260727
RANDOM_NULL_SEEDS = 200


def cost_per_side_dollars(root: str, mult: float = 1.0) -> float:
    """Baseline per-contract-side cost in dollars (commission + impact ticks)."""
    c = BY_ROOT[root]
    return mult * (COMMISSION_PER_SIDE + IMPACT_TICKS_PER_SIDE * c.tick_value)
