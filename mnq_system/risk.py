"""
Stop placement, position sizing, R:R gating, and daily/session limits per
references/risk-management.md. Numbers are conservative conventional
defaults (see mnq_system/config.py) -- not verified for any specific account.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mnq_system.config import AccountRiskConfig, ContractSpec


def get_stop(direction: str, swing_level: float, tick_size: float, buffer_ticks: int) -> float:
    buffer = buffer_ticks * tick_size
    if direction == "long":
        return swing_level - buffer
    if direction == "short":
        return swing_level + buffer
    raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")


def get_position_size(
    account_equity: float,
    entry_price: float,
    stop_price: float,
    contract: ContractSpec,
    risk_cfg: AccountRiskConfig,
) -> int:
    """Contracts to trade, rounded DOWN to whole contracts. Returns 0 (skip
    the trade) rather than rounding up if risk-per-contract exceeds budget.
    """
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0
    dollar_risk = account_equity * risk_cfg.risk_pct_per_trade
    risk_per_contract = stop_distance * contract.point_value
    if risk_per_contract <= 0:
        return 0
    contracts = max(0, math.floor(dollar_risk / risk_per_contract))
    return min(contracts, risk_cfg.max_contracts)


def reward_risk_ratio(entry_price: float, stop_price: float, target_price: float) -> float:
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return 0.0
    reward = abs(target_price - entry_price)
    return reward / risk


def meets_min_reward_risk(
    entry_price: float, stop_price: float, target_price: float, min_rr: float
) -> bool:
    return reward_risk_ratio(entry_price, stop_price, target_price) >= min_rr


@dataclass
class DailyState:
    """Mutable per-day bookkeeping the engine/live loop must reset at each
    new session.
    """

    daily_pnl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0


def check_daily_limits(state: DailyState, account_equity: float, risk_cfg: AccountRiskConfig) -> bool:
    """Returns True if trading may continue, False if the system must stand
    aside for the rest of the session.
    """
    if state.daily_pnl <= -risk_cfg.max_daily_loss_pct * account_equity:
        return False
    if state.trades_today >= risk_cfg.max_trades_per_day:
        return False
    if state.consecutive_losses >= risk_cfg.max_consecutive_losses:
        return False
    return True
