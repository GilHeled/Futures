"""
The contract every strategy and BacktestEngine share. Designed so a future
live polling loop can drive the exact same Strategy objects: bars only ever
accumulate (never rewritten), and a strategy is asked "what do you think,
given everything up to and including the latest bar" every time one closes
-- nothing here requires the full series to be known in advance except as
an optional vectorized fast path (`precompute_batch`).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from mnq_system.candlesticks import Bar


@dataclass(frozen=True)
class TimeframeSpec:
    """How a strategy declares one named bar-series it needs."""

    interval: str  # "1m"/"5m"/"15m"/"30m"/"1h" -- same vocabulary as DataProvider
    warmup_bars: int = 0  # bars of history needed before the strategy can produce a signal


class TimeframeView:
    """Causal, read-only window onto one timeframe's bars. `pos` is the
    index of "now" -- bars strictly after `pos` are never exposed, whether
    `bars` is a full backtest DataFrame or a live append-only buffer where
    `pos` always equals `len(bars) - 1`. Same class, same causality
    guarantee, no special-casing between backtest and live.
    """

    __slots__ = ("_bars", "_pos")

    def __init__(self, bars: pd.DataFrame, pos: int):
        self._bars = bars
        self._pos = pos

    @property
    def pos(self) -> int:
        return self._pos

    def _require_confirmed(self) -> None:
        if self._pos < 0:
            raise ValueError("no confirmed bar yet on this timeframe -- check `.pos >= 0` first")

    @property
    def now(self) -> pd.Timestamp:
        self._require_confirmed()
        return self._bars.index[self._pos]

    def bar(self, offset: int = 0) -> Bar:
        """offset=0 is "now"; offset=1 is the previous bar, etc."""
        self._require_confirmed()
        row = self._bars.iloc[self._pos - offset]
        return Bar(open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close))

    def visible(self) -> pd.DataFrame:
        """Every bar up to and including "now" -- never anything past it."""
        self._require_confirmed()
        return self._bars.iloc[: self._pos + 1]

    def window(self, n: int) -> pd.DataFrame:
        """The last `n` visible bars (fewer if not enough history yet)."""
        self._require_confirmed()
        lo = max(0, self._pos + 1 - n)
        return self._bars.iloc[lo : self._pos + 1]


@dataclass(frozen=True)
class MarketSnapshot:
    """What the engine hands the strategy once per driving-timeframe bar close."""

    timeframes: dict  # dict[str, TimeframeView], keyed by the names the strategy declared
    equity: float  # read-only, for diagnostics only -- the engine sizes positions, not the strategy


@dataclass
class EntrySignal:
    """A strategy's answer to "should we enter right now" -- the engine
    still applies slippage and computes position size; the strategy only
    decides direction, price levels, and what to record for later analysis.
    """

    direction: str  # "long" / "short"
    setup_type: str  # free-form label, stored on TradeRecord.setup_type
    entry_price: float
    stop_price: float
    targets: list = field(default_factory=list)  # 0..N ordered targets
    context: dict = field(default_factory=dict)  # strategy-specific diagnostic fields


@dataclass
class Position:
    direction: str
    entry_price: float
    stop_price: float  # mutable; engine applies ExitDecision.new_stop here
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    contracts: int = 0
    contracts_remaining: int = 0
    partial_taken: bool = False
    # Escape hatch for a strategy whose exit logic needs more than
    # target_1/target_2/partial_taken (e.g. a trailing stop). Not used by
    # EmaFibReversalStrategy today.
    strategy_state: dict = field(default_factory=dict)
    # Diagnostic context captured at entry -- see BacktestEngine._universal_context
    # and EntrySignal.context. Not used by any entry/exit rule.
    context: dict = field(default_factory=dict)


@dataclass
class ExitDecision:
    action: str  # opaque label; stats.py treats it as a free-form string (e.g. "stop", "full_target", ...)
    fill_price: Optional[float] = None
    fraction: float = 0.0
    new_stop: Optional[float] = None


class Strategy(abc.ABC):
    """Base class every pluggable strategy implements. `BacktestEngine`
    (and, later, a live loop) only ever calls these methods -- it has no
    knowledge of any specific strategy's indicators or rules.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable identifier for this strategy, e.g. "ema_fib_reversal" --
        used as STRATEGY_REGISTRY's key and stamped onto every
        mnq_system.signal_audit.SignalAuditEntry this strategy produces.
        """

    @property
    @abc.abstractmethod
    def timeframes(self) -> dict:
        """dict[str, TimeframeSpec] -- the named bar-series this strategy needs."""

    @property
    @abc.abstractmethod
    def driving_timeframe(self) -> str:
        """Key into `timeframes`: the engine iterates bar-by-bar on this one."""

    def precompute_batch(self, full_history: dict) -> None:
        """Optional fast path. The backtest engine calls this once at
        construction with the complete bars for every declared timeframe,
        so a strategy can vectorize instead of paying per-bar cost. Must be
        equivalent to driving `on_bar` bar-by-bar from empty state -- a
        cache-warmer, not a second source of truth. Default: no-op (a
        strategy with no batch-friendly precomputation need not override this).
        """
        return

    @abc.abstractmethod
    def on_bar(self, snapshot: MarketSnapshot) -> None:
        """Called once per driving-timeframe bar close, before check_entry/
        check_exit are consulted for that bar (regardless of whether a
        position is open). Strategy updates ITS OWN persistent state here
        if it needs continuous tracking; a strategy whose state only needs
        updating while flat may leave this a no-op and do it all in
        check_entry instead.
        """

    @abc.abstractmethod
    def check_entry(self, snapshot: MarketSnapshot) -> Optional[EntrySignal]:
        """Only ever called while flat -- a strategy never needs to reason
        about an existing position here.
        """

    @abc.abstractmethod
    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        """`session_ending` is informational -- the engine itself forces a
        flatten when the account's session policy calls for it, before this
        is even consulted for that case.
        """

    def diagnostic_dimensions(self) -> list:
        """Context keys this strategy wants broken down via
        backtest.stats.breakdown_by/full_breakdown_report. Default: none.
        """
        return []
