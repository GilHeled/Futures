"""FROZEN configuration for ict_live (Phase 1: analysis/recommendation only).

Encodes the decisions in docs/FROZEN_DECISIONS.md. Each value carries a label:
  COURSE  — fixed by the course methodology
  NEC     — necessary mechanization (course gives no number; provisional value)
  RES     — research choice (structural decision made with the user; never P&L-selected)
Deferred numeric/mechanical choices are DEFERRED sentinels: using one raises loudly, so the
engine can never silently substitute an unapproved number (see FROZEN_DECISIONS §3).
Do not change a frozen value to improve results; changes require a documented reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Optional


class _Deferred:
    """Sentinel for a decision explicitly NOT frozen yet. Any use raises."""
    __slots__ = ("name", "note")

    def __init__(self, name: str, note: str = ""):
        self.name = name
        self.note = note

    def __repr__(self):  # pragma: no cover
        return f"DEFERRED({self.name!r})"

    def _raise(self, *_a, **_k):
        raise NotImplementedError(
            f"ict_live: parameter '{self.name}' is DEFERRED (not frozen). {self.note} "
            f"See docs/FROZEN_DECISIONS.md §3 — must be decided (never P&L-selected) before use."
        )
    # any arithmetic/comparison/call use trips the guard
    __float__ = __int__ = __call__ = __lt__ = __le__ = __gt__ = __ge__ = _raise
    __add__ = __sub__ = __mul__ = __truediv__ = __radd__ = __rmul__ = _raise


def DEFERRED(name: str, note: str = "") -> _Deferred:
    return _Deferred(name, note)


# ---------------------------------------------------------------- timeframes (B1)
TF_ROLES = {                                    # label: RES (frozen B1)
    "W": "htf_context", "D": "htf_context", "4H": "htf_context",
    "1H": "primary_analysis",
    "15m": "intraday_structure",                # lowest TF for significant swings/liquidity
    "5m": "entry_refinement_optional",
}
BUILT_TIMEFRAMES = ("5m", "15m", "1H", "4H", "D", "W")   # constructed from the 1m stream
DEFAULT_SIGNAL_TF = "1H"                         # configurable; recorded per candidate (B1)
ALLOWED_SIGNAL_TFS = ("1H", "15m")               # 5m only refines, never originates (B1)

# ---------------------------------------------------------------- sessions (C1, NEC)
# ET, DST calendar-aware (resolved per-date, NOT a fixed 7h offset). (start, end) inclusive-open.
SESSION_TZ = "America/New_York"
SESSIONS = {                                     # full sessions vs active windows are distinct
    "asia":    (time(18, 0), time(0, 0)),        # 18:00 -> 00:00 (next day)
    "london_active":  (time(2, 0), time(5, 0)),
    "ny_am":   (time(8, 30), time(11, 0)),
    "ny_pm":   (time(13, 30), time(16, 0)),
}

# ---------------------------------------------------------------- structure (C2, NEC)
FRACTAL_WIDTH = {"1H": 2, "15m": 2, "minor": 1}  # CANDIDATE pivots only; != significant swing

# ---------------------------------------------------------------- liquidity (C4, NEC)
EQUAL_HL_TOL_ATR = 0.15                           # x ATR(TF); provisional; record actual dist+ATR

# ---------------------------------------------------------------- PD arrays (C9/C10, COURSE)
NWOG_KEEP = 3                                     # keep 3 NWOGs (~3 weeks); S/R/magnet, not ERL
ORG_TRACK_CURRENT_DAY_ONLY = True                 # current-day ORG + its 50%; no history
# context-only course statistics (never hard rules):
NWOG_REBALANCE_NOTE_PTS = (60, 200)
ORG_REBALANCE_NOTE_PCT = 70

# ---------------------------------------------------------------- targets / entry (B4/B8)
MIN_RR = 3.0                                      # COURSE/RES (B4): reject < 3R; keep >3R target
ENTRY_MODE = "CE"                                 # B8 production; {"proximal","CE","distal"}
ENTRY_MODES = ("proximal", "CE", "distal")        # configurable + recorded per candidate
FIXED_3R_RESEARCH_MODE = False                    # B4: alternate research mode only

# valid target hierarchy (B4) — ERL/named liquidity; IRL is intermediate-only
TARGET_HIERARCHY = (
    "opposing_significant_erl", "PDH", "PDL", "PWH", "PWL",
    "asia_hl_active", "london_hl_active", "equal_highs_lows", "significant_15m_swing", "htf_objective",
)

# ---------------------------------------------------------------- deferred (FROZEN_DECISIONS §3)
SIGNIFICANT_SWING_MAGNITUDE = DEFERRED("significant_swing_magnitude", "B2/C3: structural for now")
CONSOLIDATION_DETECTOR = DEFERRED("consolidation_detector", "B7: accumulation numeric detector")
DISPLACEMENT_QUALITY = DEFERRED("displacement_quality_threshold", "B5/C6: leg extent frozen, quality not")
SWEEP_TOL = DEFERRED("sweep_tol", "C5: single named-level penetration tolerance")
STOP_BUFFER = DEFERRED("stop_buffer", "C7: instrument-aware execution buffer")
HTF_REVERSING = DEFERRED("htf_reversing", "B10: genuine HTF transition, not one isolated MSS")
MAX_SETUP_AGE = DEFERRED("max_setup_age", "C8: optional housekeeping cap; primary expiry is structural")
MSS_ACCEPTANCE_THRESHOLD = DEFERRED("mss_acceptance_threshold", "B6: diagnostic-only for now")
QUALITY_BOUNDARIES = DEFERRED("quality_ABC_boundaries", "B11: provisional qualitative tiers only")

# ---------------------------------------------------------------- hard invariants (§D)
CAUSAL_ONLY = True            # no look-ahead; prefix-stability test mandatory
HTF_IS_VETO = False           # B10 invariant: HTF context is NEVER a hard veto
ALLOW_PNL_TUNING = False      # never P&L-select during implementation
NO_TRADE_IS_FIRST_CLASS = True


@dataclass(frozen=True)
class Instrument:
    symbol: str                 # TradingView id, e.g. "CME_MINI:NQ1!"
    root: str                   # "NQ"
    tick_size: float
    point_value: float


# minimal registry; extend as feeds are added
INSTRUMENTS = {
    "CME_MINI:NQ1!": Instrument("CME_MINI:NQ1!", "NQ", 0.25, 20.0),
    "CME_MINI:ES1!": Instrument("CME_MINI:ES1!", "ES", 0.25, 50.0),
    "CME_MINI:MNQ1!": Instrument("CME_MINI:MNQ1!", "MNQ", 0.25, 2.0),
    "CME_MINI:MES1!": Instrument("CME_MINI:MES1!", "MES", 0.25, 5.0),
}
