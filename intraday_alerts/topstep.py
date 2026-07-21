"""
Prospective Topstep-rule risk simulator (Combine → Express Funded, $50k).

Enforces the frozen rules PROSPECTIVELY — a trade is BLOCKED before entry if
its worst-case loss (the stop) could breach the effective (buffered) daily
loss limit or the trailing maximum loss limit. At fixed 1-micro sizing there
is no "size-reduce" — enforcement is block-only. Reports both prevented
breaches and resulting net performance.

Also enforces the alert-selection policy (one position, ≤ N/day, cooldown,
deterministic order) since that determines which candidates are realized.

MLL model (verified rules): the real floor = min(EOD-high-water − $2,000,
start_balance) — it trails the end-of-day balance up, never down, and locks
at the start balance. The *effective* floor sits `($2,000 − eff_max_loss)`
above the real floor (our 20% safety buffer ⇒ stop at $1,600 loss, not
$2,000). Monitored intraday in real time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from intraday_alerts import config as C


@dataclass
class TopstepRiskState:
    start_balance: float = float(C.TOPSTEP_ACCOUNT_SIZE)
    real_max_loss: float = float(C.TOPSTEP_MAX_LOSS_LIMIT)   # $2,000
    eff_max_loss: float = float(C.EFFECTIVE_MAX_LOSS)        # $1,600 (buffered)
    eff_daily_stop: float = float(C.EFFECTIVE_DAILY_STOP)    # $800  (buffered)
    balance: float = field(init=False)
    eod_hwm: float = field(init=False)
    day_pnl: float = field(default=0.0, init=False)
    day_halted: bool = field(default=False, init=False)

    def __post_init__(self):
        self.balance = self.start_balance
        self.eod_hwm = self.start_balance

    def _real_floor(self) -> float:
        return min(self.eod_hwm - self.real_max_loss, self.start_balance)

    def _eff_floor(self) -> float:
        return self._real_floor() + (self.real_max_loss - self.eff_max_loss)

    @property
    def locked(self) -> bool:
        return (self.eod_hwm - self.real_max_loss) >= self.start_balance

    def can_enter(self, worst_case_loss: float) -> bool:
        if self.day_halted:
            return False
        if (self.day_pnl - worst_case_loss) <= -self.eff_daily_stop:   # would breach daily
            return False
        if (self.balance - worst_case_loss) <= self._eff_floor():      # would breach trailing MLL
            return False
        return True

    def register_exit(self, pnl: float):
        self.balance += pnl
        self.day_pnl += pnl
        if self.day_pnl <= -self.eff_daily_stop:
            self.day_halted = True   # forced break for the session

    def end_day(self):
        self.eod_hwm = max(self.eod_hwm, self.balance)   # trails up only
        self.day_pnl = 0.0
        self.day_halted = False


def simulate_alert_sequence(candidates, point_value: float,
                            max_per_day: int = C.MAX_ENTRIES_PER_DAY,
                            cooldown_bars: int = C.COOLDOWN_BARS):
    """`candidates`: chronologically ordered dicts with entry_pos, exit_pos,
    et_date, direction ('long'/'short'), entry_price, stop_price, exit_price.
    Applies one-position + cooldown + ≤max/day + prospective Topstep blocking.
    Returns (realized: list[dict with pnl], report: dict)."""
    state = TopstepRiskState()
    realized, prevented_breaches, day_halts = [], 0, 0
    cur_day, entries_today, busy_until_pos = None, 0, -1

    for c in candidates:
        if c["et_date"] != cur_day:
            if cur_day is not None:
                if state.day_halted:
                    day_halts += 1
                state.end_day()
            cur_day, entries_today, busy_until_pos = c["et_date"], 0, -1
        if c["entry_pos"] <= busy_until_pos or entries_today >= max_per_day:
            continue
        size = C.RESEARCH_SIZE_MICROS
        worst_case_loss = abs(c["entry_price"] - c["stop_price"]) * point_value * size
        if not state.can_enter(worst_case_loss):
            prevented_breaches += 1
            continue
        sign = 1 if c["direction"] == "long" else -1
        pnl = (c["exit_price"] - c["entry_price"]) * sign * point_value * size
        state.register_exit(pnl)
        realized.append({**c, "pnl": pnl})
        entries_today += 1
        busy_until_pos = c["exit_pos"] + cooldown_bars
    if cur_day is not None:
        state.end_day()

    return realized, {
        "n_realized": len(realized),
        "prevented_breaches": prevented_breaches,
        "day_halts": day_halts,
        "final_balance": state.balance,
        "mll_locked": state.locked,
    }
