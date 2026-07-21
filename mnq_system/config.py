"""
Account/instrument-level configuration, shared across every strategy.

Strategy-specific parameters (indicator periods, entry/exit mechanics,
which timeframes it needs, etc.) live with each strategy in
mnq_system/strategies/<name>/config.py -- they're a strategy's own tunable
inputs, not something the account or engine has an opinion about.

IMPORTANT: numeric defaults here are conventional starting points, not
verified/optimal values -- see docs/SPEC.md's verification workflow.
Contract specs are illustrative -- confirm the current, correct figures
with your broker/CME before relying on them for real position sizing.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ContractSpec:
    """MNQ (Micro E-mini Nasdaq-100) -- CONFIRM with broker/CME before live use."""

    symbol: str = "MNQ"
    tick_size: float = 0.25
    point_value: float = 2.0  # USD per index point, per contract


@dataclass(frozen=True)
class SessionConfig:
    # All times ET (US/Eastern), (start_h, start_m, end_h, end_m). Account
    # -level trading-schedule policy -- read directly by the engine, not
    # just passed through to strategy logic, and shared across strategies
    # trading the same instrument/account.
    trading_windows: tuple = ((9, 30, 11, 30), (15, 0, 16, 0))
    reduced_size_windows: tuple = ((12, 0, 13, 30),)
    flatten_before_close: bool = True
    timezone: str = "America/New_York"


@dataclass(frozen=True)
class AccountRiskConfig:
    risk_pct_per_trade: float = 0.005  # 0.5% of equity
    max_daily_loss_pct: float = 0.03
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 5
    max_concurrent_positions: int = 1
    # Absolute backstop on position size, independent of the risk-budget
    # formula -- guards against a degenerate (near-zero) stop distance
    # sizing up to an unrealistic number of contracts.
    max_contracts: int = 20


@dataclass(frozen=True)
class AccountConfig:
    contract: ContractSpec = field(default_factory=ContractSpec)
    session: SessionConfig = field(default_factory=SessionConfig)
    risk: AccountRiskConfig = field(default_factory=AccountRiskConfig)


DEFAULT_ACCOUNT_CONFIG = AccountConfig()

# Published CME micro-futures specs (CONFIRM with broker/CME before live use,
# same caveat as ContractSpec itself) -- used to resolve the correct
# tick_size/point_value for a --symbol other than MNQ. R-multiple/profit-
# factor-based comparisons are invariant to point_value (position sizing
# scales inversely with it), but tick_size directly affects realistic-cost
# slippage sizing and must be correct per instrument for a cross-market
# cost comparison to be meaningful.
CONTRACT_SPECS = {
    "MNQ": ContractSpec(symbol="MNQ", tick_size=0.25, point_value=2.0),
    "MES": ContractSpec(symbol="MES", tick_size=0.25, point_value=5.0),
    "MYM": ContractSpec(symbol="MYM", tick_size=1.0, point_value=0.5),
    "M2K": ContractSpec(symbol="M2K", tick_size=0.10, point_value=5.0),
}
