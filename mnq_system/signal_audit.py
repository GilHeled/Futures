"""
Signal audit log: one entry per actual entry/exit recommendation a Strategy
makes, regardless of whether the engine acted on it. Exists so a replay (or,
later, a live) run leaves a complete, inspectable trail -- not just the
trades that were ultimately filled -- for verifying causal behavior and
diagnosing why a signal was or wasn't taken.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Optional

import pandas as pd


@dataclass
class SignalAuditEntry:
    timestamp: pd.Timestamp
    symbol: str
    strategy_name: str
    timeframe: str  # the driving timeframe's interval, e.g. "5m"
    signal_type: str  # "entry" | "exit"
    reason: str  # setup_type (entry) or ExitDecision.action (exit)
    disposition: str  # "accepted" | "blocked_sizing_zero"
    direction: Optional[str] = None
    stop_price: Optional[float] = None
    targets: Optional[list] = None
    risk_dollars: Optional[float] = None
    contracts: Optional[int] = None
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def audit_log_fieldnames() -> list:
    return [f.name for f in fields(SignalAuditEntry)]


def audit_log_to_dataframe(audit_log: list) -> pd.DataFrame:
    return pd.DataFrame([entry.to_dict() for entry in audit_log], columns=audit_log_fieldnames())
