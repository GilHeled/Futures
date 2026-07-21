"""
Historical replay: drives a Strategy bar-by-bar through
`BacktestEngine.step()` -- the exact same per-bar decision path
`BacktestEngine.run()` uses -- while recording a complete signal audit log
(mnq_system/signal_audit.py) of every entry/exit recommendation, accepted or
blocked, not just the trades that were ultimately filled.

This module exists to prove three things before any live data feed is
connected: replay decisions match backtest decisions exactly (same engine,
same step()), the strategy is causal (no bar's decision depends on bars
after it), and every recommendation leaves an inspectable trail. The
`step()` seam this reuses is also the one a future live loop would call
once per incoming bar -- nothing here is replay-specific except how bars
are supplied (all at once, from history, rather than one at a time from a
live feed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from mnq_system.backtest.engine import BacktestEngine, BacktestResult, BacktestSettings, TradeRecord
from mnq_system.config import AccountConfig
from mnq_system.signal_audit import SignalAuditEntry
from mnq_system.strategy_api import Strategy


@dataclass
class ReplayResult:
    trades: list[TradeRecord]
    equity_curve: pd.Series
    final_equity: float
    audit_log: list[SignalAuditEntry]


def run_replay(
    bars_by_timeframe: dict,
    strategy: Strategy,
    account: AccountConfig,
    settings: Optional[BacktestSettings] = None,
    symbol: str = "MNQ",
) -> ReplayResult:
    audit_log: list[SignalAuditEntry] = []
    engine = BacktestEngine(bars_by_timeframe, strategy, account, settings, audit_log=audit_log, symbol=symbol)
    result: BacktestResult = engine.run()
    return ReplayResult(
        trades=result.trades,
        equity_curve=result.equity_curve,
        final_equity=result.final_equity,
        audit_log=audit_log,
    )
