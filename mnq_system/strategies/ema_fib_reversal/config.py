"""
Configuration for EmaFibReversalStrategy: 15m EMA-stack bias filter, 5m
Fibonacci-retracement pullback entries, and break-of-structure reversal
entries. See docs/SPEC.md for the full rule-set writeup.

IMPORTANT: every numeric default here is a conventional starting hypothesis
borrowed from common technical-analysis practice, not a verified/optimal
value -- see docs/SPEC.md's verification workflow. A rigorous ~7-year
backtest of this exact strategy found no demonstrated persistent edge (see
project memory); these defaults are kept as a reference implementation of
the Strategy interface (mnq_system/strategy_api.py), not a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mnq_system.candlesticks import CandleConfig


@dataclass(frozen=True)
class EmaConfig:
    fast: int = 9
    mid: int = 20
    slow: int = 50
    slope_lookback: int = 10


@dataclass(frozen=True)
class FibConfig:
    golden_low: float = 0.5
    golden_high: float = 0.618
    shallow: float = 0.382
    invalidation: float = 0.786
    ext_target_1: float = 1.272
    ext_target_2: float = 1.618
    ema_confluence_atr_mult: float = 1.0


@dataclass(frozen=True)
class SwingConfig:
    # Fractal window: a bar is a confirmed swing high/low once this many
    # bars on each side are strictly lower/higher. Causal: confirmation
    # lags `lookback` bars behind the actual pivot, by design (no lookahead).
    lookback: int = 2


@dataclass(frozen=True)
class EmaFibReversalExitConfig:
    """Entry/exit-shape mechanics specific to this strategy's setups --
    NOT account-level risk limits (those live in mnq_system.config.AccountRiskConfig).
    """

    min_reward_risk: float = 1.5
    reversal_min_reward_risk: float = 2.0
    stop_buffer_ticks: int = 2
    # Floor on the reversal stop distance, as a fraction of ATR(14). Without
    # this, a retest that closes very near the broken structure level can
    # produce a near-zero stop, which the position sizer then "compensates"
    # for with an unrealistically large contract count.
    reversal_min_stop_atr_mult: float = 0.5
    partial_exit_fraction: float = 0.5  # fraction closed at first extension target


@dataclass(frozen=True)
class EmaFibReversalConfig:
    ema: EmaConfig = field(default_factory=EmaConfig)
    fib: FibConfig = field(default_factory=FibConfig)
    swing: SwingConfig = field(default_factory=SwingConfig)
    candle: CandleConfig = field(default_factory=CandleConfig)
    exit: EmaFibReversalExitConfig = field(default_factory=EmaFibReversalExitConfig)
    bias_timeframe: str = "15m"
    entry_timeframe: str = "5m"
    enable_reversal_entries: bool = True
    enable_pullback_entries: bool = True
    # Data-driven behavioral filter under test (see docs/SPEC.md verification
    # notes): a 4-year backtest showed reversal-long entries taken while the
    # 15m bias still read bearish were the weakest bucket in the whole
    # system (9 trades, avg R -0.58). This does NOT change the existing
    # anti-continuation gate (which already blocks a reversal from firing
    # in the SAME direction as the prevailing bias) -- it additionally
    # blocks the specific case of a bullish reversal firing against a
    # bearish bias, based on the empirical finding, not a design principle.
    filter_reversal_long_vs_bearish_bias: bool = False


DEFAULT_CONFIG = EmaFibReversalConfig()
