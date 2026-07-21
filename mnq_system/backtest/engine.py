"""
Event-driven backtest engine. Walks the driving-timeframe bars (as declared
by the active `Strategy`) in chronological order, one bar at a time, and at
each step only consults information that would genuinely have been
available at that bar's close -- causal cross-timeframe alignment lives in
mnq_system/timeframe_alignment.py, and every non-driving timeframe view is
built from it.

The engine is strategy-agnostic: it owns the loop, equity/PnL bookkeeping,
daily risk limits, session-window gating, and slippage/commission -- all
account-level concerns -- and calls into the active `Strategy` (see
mnq_system/strategy_api.py) for entry/exit decisions. This is the only
place a `Strategy`'s decisions get wired to mutable state (open position,
daily counters), so both `mnq_system backtest` and a future live loop can
share the same `Strategy` implementations.

`run()` is a thin loop around `step(j)` -- the same per-bar seam
`mnq_system/replay.py` (and, later, a live loop) drives directly, one bar at
a time, guaranteeing replay/live decisions can never drift from backtest
decisions since they're the exact same code path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time as dt_time
from typing import Optional

import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.risk import DailyState, check_daily_limits, get_position_size
from mnq_system.signal_audit import SignalAuditEntry
from mnq_system.strategy_api import EntrySignal, ExitDecision, MarketSnapshot, Position, Strategy, TimeframeView
from mnq_system.timeframe_alignment import as_of_pos, bar_end_index, interval_timedelta


@dataclass
class BacktestSettings:
    account_equity: float = 50_000.0
    commission_per_contract: float = 0.0  # round-turn $ per contract; confirm with your broker
    slippage_ticks: float = 0.0  # applied against you on both entries and exits


@dataclass
class TradeRecord:
    setup_type: str
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    contracts: int
    pnl: float
    r_multiple: float
    exit_reason: str
    # Diagnostic context captured at entry -- see BacktestEngine._universal_context
    # and each Strategy's own EntrySignal.context.
    context: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    equity_curve: pd.Series
    final_equity: float


def _within_windows(et_time: dt_time, windows: tuple) -> bool:
    for sh, sm, eh, em in windows:
        if dt_time(sh, sm) <= et_time < dt_time(eh, em):
            return True
    return False


class BacktestEngine:
    def __init__(
        self,
        bars_by_timeframe: dict,
        strategy: Strategy,
        account: AccountConfig,
        settings: Optional[BacktestSettings] = None,
        audit_log: Optional[list] = None,
        symbol: str = "",
    ):
        missing = [name for name in strategy.timeframes if name not in bars_by_timeframe]
        if missing:
            raise ValueError(f"bars_by_timeframe is missing timeframe(s) required by the strategy: {missing}")

        self.bars_by_timeframe = bars_by_timeframe
        self.strategy = strategy
        self.account = account
        self.settings = settings or BacktestSettings()
        self.tick_size = account.contract.tick_size
        # When provided, one mnq_system.signal_audit.SignalAuditEntry is
        # appended per actual entry/exit recommendation (accepted or
        # blocked) -- see step(). None (the default) means zero overhead and
        # zero behavior change versus before this parameter existed.
        self.audit_log = audit_log
        self.symbol = symbol

        self.driving_name = strategy.driving_timeframe
        self.driving_bars = bars_by_timeframe[self.driving_name]
        self.driving_interval = strategy.timeframes[self.driving_name].interval
        self.driving_interval_td = interval_timedelta(self.driving_interval)

        self._bar_end = {
            name: bar_end_index(bars_by_timeframe[name].index, spec.interval)
            for name, spec in strategy.timeframes.items()
        }

        strategy.precompute_batch(bars_by_timeframe)

        # Run-state, (re)initialized by _init_run_state() at the start of run().
        self.equity = 0.0
        self.equity_points: list[tuple[pd.Timestamp, float]] = []
        self.trades: list[TradeRecord] = []
        self.position: Optional[Position] = None
        self.position_setup_type = ""
        self.realized_pnl_open_trade = 0.0
        self.entry_time_open_trade: Optional[pd.Timestamp] = None
        self.entry_risk_dollars_open_trade = 0.0
        self.exit_fills_open_trade: list[tuple[float, int]] = []
        self.daily_state = DailyState()
        self.current_day = None

    # ---------------------------------------------------------------- utils

    def _snapshot(self, j: int, equity: float) -> MarketSnapshot:
        t = self.driving_bars.index[j]
        views = {}
        for name in self.strategy.timeframes:
            bars = self.bars_by_timeframe[name]
            pos = j if name == self.driving_name else as_of_pos(self._bar_end[name], t)
            views[name] = TimeframeView(bars, pos)
        return MarketSnapshot(timeframes=views, equity=equity)

    def _is_session_ending(self, j: int) -> bool:
        t = self.driving_bars.index[j]
        et = t.tz_convert(self.account.session.timezone)
        end_h, end_m = self.account.session.trading_windows[-1][2], self.account.session.trading_windows[-1][3]
        session_end_today = et.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        if (et + self.driving_interval_td) >= session_end_today:
            return True
        if j + 1 >= len(self.driving_bars):
            return True
        next_et = self.driving_bars.index[j + 1].tz_convert(self.account.session.timezone)
        return next_et.date() != et.date()

    def _apply_slippage(self, price: float, direction: str, is_entry: bool) -> float:
        ticks = self.settings.slippage_ticks
        if ticks <= 0:
            return price
        adverse = ticks * self.tick_size
        worse_when_long = is_entry  # buying: slippage raises entry price
        if direction == "long":
            return price + adverse if worse_when_long else price - adverse
        return price - adverse if worse_when_long else price + adverse

    def _universal_context(self, t: pd.Timestamp, entry_price: float, stop_price: float, targets: list) -> dict:
        et = t.tz_convert(self.account.session.timezone)
        return {
            "entry_weekday": et.strftime("%A"),
            "entry_hour_et": et.hour,
            "initial_entry_price": entry_price,
            "initial_stop_price": stop_price,
            "initial_targets": list(targets),
        }

    def _open_position(self, signal: EntrySignal, equity: float, t: pd.Timestamp):
        entry_price = self._apply_slippage(signal.entry_price, signal.direction, is_entry=True)
        contracts = get_position_size(equity, entry_price, signal.stop_price, self.account.contract, self.account.risk)
        if contracts <= 0:
            return None

        context = {**self._universal_context(t, entry_price, signal.stop_price, signal.targets), **signal.context}
        position = Position(
            direction=signal.direction,
            entry_price=entry_price,
            stop_price=signal.stop_price,
            target_1=signal.targets[0] if len(signal.targets) > 0 else None,
            target_2=signal.targets[1] if len(signal.targets) > 1 else None,
            contracts=contracts,
            contracts_remaining=contracts,
            context=context,
        )
        risk_dollars = abs(position.entry_price - position.stop_price) * contracts * self.account.contract.point_value
        return position, signal.setup_type, risk_dollars

    # ---------------------------------------------------------------- audit log

    def _record_entry_audit(self, t: pd.Timestamp, signal: EntrySignal, opened) -> None:
        if self.audit_log is None:
            return
        if opened is None:
            entry = SignalAuditEntry(
                timestamp=t, symbol=self.symbol, strategy_name=self.strategy.name,
                timeframe=self.driving_interval, signal_type="entry", reason=signal.setup_type,
                disposition="blocked_sizing_zero", direction=signal.direction, stop_price=signal.stop_price,
                targets=list(signal.targets), context=dict(signal.context),
            )
        else:
            position, setup_type, risk_dollars = opened
            entry = SignalAuditEntry(
                timestamp=t, symbol=self.symbol, strategy_name=self.strategy.name,
                timeframe=self.driving_interval, signal_type="entry", reason=setup_type, disposition="accepted",
                direction=position.direction, stop_price=position.stop_price,
                targets=[v for v in (position.target_1, position.target_2) if v is not None],
                risk_dollars=risk_dollars, contracts=position.contracts, context=dict(position.context),
            )
        self.audit_log.append(entry)

    def _record_exit_audit(self, t: pd.Timestamp, decision: ExitDecision) -> None:
        if self.audit_log is None:
            return
        position = self.position
        entry = SignalAuditEntry(
            timestamp=t, symbol=self.symbol, strategy_name=self.strategy.name,
            timeframe=self.driving_interval, signal_type="exit", reason=decision.action, disposition="accepted",
            direction=position.direction, stop_price=position.stop_price,
            targets=[v for v in (position.target_1, position.target_2) if v is not None],
            contracts=position.contracts_remaining, context=dict(position.context),
        )
        self.audit_log.append(entry)

    # ---------------------------------------------------------------- run

    def _init_run_state(self) -> None:
        self.equity = self.settings.account_equity
        self.equity_points = []
        self.trades = []
        self.position = None
        self.position_setup_type = ""
        self.realized_pnl_open_trade = 0.0
        self.entry_time_open_trade = None
        self.entry_risk_dollars_open_trade = 0.0
        self.exit_fills_open_trade = []
        self.daily_state = DailyState()
        self.current_day = None

    def step(self, j: int) -> None:
        """Process exactly one driving-timeframe bar, mutating engine state
        in place. The sole per-bar seam: `run()` calls this once per
        historical index; `mnq_system.replay.run_replay` (and, later, a live
        loop) calls it once per bar as it arrives -- same method, same
        decisions.
        """
        t = self.driving_bars.index[j]
        et = t.tz_convert(self.account.session.timezone)

        if self.current_day is not None and et.date() != self.current_day:
            self.daily_state = DailyState()
        self.current_day = et.date()

        session_ending = self._is_session_ending(j)
        snapshot = self._snapshot(j, self.equity)
        self.strategy.on_bar(snapshot)

        # ---- manage an existing position first ----
        if self.position is not None:
            if session_ending and self.account.session.flatten_before_close:
                close_price = float(self.driving_bars["close"].iloc[j])
                decision = ExitDecision(action="session_flatten", fill_price=close_price, fraction=1.0)
            else:
                decision = self.strategy.check_exit(snapshot, self.position, session_ending)

            if decision.action != "none":
                self._record_exit_audit(t, decision)
                fill = self._apply_slippage(decision.fill_price, self.position.direction, is_entry=False)
                if decision.fraction >= 1.0:
                    contracts_closed = self.position.contracts_remaining
                else:
                    contracts_closed = min(
                        self.position.contracts_remaining,
                        max(1, int(round(self.position.contracts * decision.fraction))),
                    )

                sign = 1 if self.position.direction == "long" else -1
                pnl = (fill - self.position.entry_price) * sign * contracts_closed * self.account.contract.point_value
                pnl -= self.settings.commission_per_contract * contracts_closed
                self.realized_pnl_open_trade += pnl
                self.exit_fills_open_trade.append((fill, contracts_closed))
                self.position.contracts_remaining -= contracts_closed
                self.equity += pnl
                self.daily_state.daily_pnl += pnl

                if decision.new_stop is not None:
                    self.position.stop_price = decision.new_stop
                if decision.action == "partial_target":
                    self.position.partial_taken = True

                if self.position.contracts_remaining <= 0:
                    total_closed = sum(c for _, c in self.exit_fills_open_trade)
                    weighted_exit = sum(p * c for p, c in self.exit_fills_open_trade) / total_closed
                    risk_dollars = max(1e-9, self.entry_risk_dollars_open_trade)
                    self.trades.append(
                        TradeRecord(
                            setup_type=self.position_setup_type,
                            direction=self.position.direction,
                            entry_time=self.entry_time_open_trade,
                            exit_time=t,
                            entry_price=self.position.entry_price,
                            exit_price=weighted_exit,
                            contracts=self.position.contracts,
                            pnl=self.realized_pnl_open_trade,
                            r_multiple=self.realized_pnl_open_trade / risk_dollars,
                            exit_reason=decision.action,
                            context=self.position.context,
                        )
                    )
                    self.daily_state.trades_today += 1
                    self.daily_state.consecutive_losses = (
                        self.daily_state.consecutive_losses + 1 if self.realized_pnl_open_trade < 0 else 0
                    )
                    self.position = None
                    self.realized_pnl_open_trade = 0.0
                    self.exit_fills_open_trade = []
                    self.entry_time_open_trade = None
                    self.entry_risk_dollars_open_trade = 0.0

        self.equity_points.append((t, self.equity))

        # ---- look for a new entry only if flat ----
        if (
            self.position is None
            and not session_ending
            and check_daily_limits(self.daily_state, self.equity, self.account.risk)
        ):
            et_time = et.time()
            in_window = _within_windows(et_time, self.account.session.trading_windows)
            in_reduced = _within_windows(et_time, self.account.session.reduced_size_windows)

            if in_window or in_reduced:
                signal = self.strategy.check_entry(snapshot)
                if signal is not None:
                    opened = self._open_position(signal, self.equity, t)
                    self._record_entry_audit(t, signal, opened)
                    if opened is not None:
                        self.position, self.position_setup_type, self.entry_risk_dollars_open_trade = opened
                        self.entry_time_open_trade = t

    def _finalize(self) -> BacktestResult:
        equity_curve = pd.Series(
            [e for _, e in self.equity_points], index=[ts for ts, _ in self.equity_points], name="equity"
        )
        return BacktestResult(trades=self.trades, equity_curve=equity_curve, final_equity=self.equity)

    def run(self) -> BacktestResult:
        self._init_run_state()
        for j in range(len(self.driving_bars)):
            self.step(j)
        return self._finalize()
