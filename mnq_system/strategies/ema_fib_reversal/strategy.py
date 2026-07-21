"""
EmaFibReversalStrategy: 15m EMA-bias filter gating 5m Fibonacci-retracement
pullback entries and break-of-structure reversal entries. Combines bias +
fib/EMA location + candlestick confirmation into entry signals, and fib
extensions + stop + opposing signals into exit decisions.

The module-level `check_pullback_entry`/`check_reversal_entry`/`check_exit`
functions are pure decision functions (given today's bar/state, what should
happen); `EmaFibReversalStrategy` is the stateful adapter that implements
`mnq_system.strategy_api.Strategy` on top of them, so both the backtest
engine and a future live loop can drive it identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from mnq_system.candlesticks import Bar, is_bearish_engulfing, is_bullish_engulfing, is_hammer, is_shooting_star
from mnq_system.config import AccountConfig
from mnq_system.indicators import atr, ema, session_vwap
from mnq_system.regime import (
    TREND_PERSISTENCE_BARS_THRESHOLD,
    bucket_percentile,
    consecutive_run_length,
    rolling_percentile,
)
from mnq_system.risk import get_stop, meets_min_reward_risk
from mnq_system.strategies.ema_fib_reversal.bias import (
    BEARISH,
    BULLISH,
    get_bias,
    precompute_bias_inputs,
)
from mnq_system.strategies.ema_fib_reversal.config import EmaFibReversalConfig
from mnq_system.strategies.ema_fib_reversal.fibonacci import FibLevels, get_fib_levels, has_ema_confluence, in_golden_zone, in_shallow_zone, is_invalidated
from mnq_system.strategy_api import EntrySignal, ExitDecision, MarketSnapshot, Position, Strategy, TimeframeSpec
from mnq_system.swings import compute_swings, detect_bos, find_impulse_leg, latest_confirmed_swing

LONG, SHORT = "long", "short"
VOLATILITY_LOOKBACK_BARS = 2000  # ~1 week of continuous 5m bars; reporting-only, not a trading rule
REVERSAL_SETUP_TIMEOUT_BARS = 20


# ---------------------------------------------------------------- pure decision functions

def check_pullback_entry(
    bias: str,
    price: float,
    fib_levels: FibLevels,
    ema_mid_value: float,
    atr_value: float,
    prev_bar: Bar,
    curr_bar: Bar,
    cfg: EmaFibReversalConfig,
) -> Optional[str]:
    """Continuation entry: shallow retracement into the golden zone (or the
    shallow 38.2% zone, valid under any bias since strength is judged by the
    caller passing the right zone), confirmed by EMA confluence and a
    confirmation candle, traded WITH the bias. Never fires against bias.
    """
    if bias not in (BULLISH, BEARISH):
        return None
    if is_invalidated(price, fib_levels):
        return None
    if not (in_golden_zone(price, fib_levels) or in_shallow_zone(price, fib_levels)):
        return None
    if not has_ema_confluence(price, ema_mid_value, atr_value, cfg.fib):
        return None

    if bias == BULLISH:
        confirmed = is_bullish_engulfing(prev_bar, curr_bar) or is_hammer(curr_bar, cfg.candle)
        return LONG if confirmed else None
    confirmed = is_bearish_engulfing(prev_bar, curr_bar) or is_shooting_star(curr_bar, cfg.candle)
    return SHORT if confirmed else None


@dataclass
class ReversalSetup:
    """Tracks a break-of-structure while it waits for a failed retest."""

    direction: str  # "bullish" or "bearish" -- direction of the character change
    broken_level: float
    retest_tolerance: float

    def check_retest(self, bar: Bar) -> bool:
        near = (
            abs(bar.high - self.broken_level) <= self.retest_tolerance
            or abs(bar.low - self.broken_level) <= self.retest_tolerance
        )
        if not near:
            return False
        if self.direction == "bullish":
            return bar.close > self.broken_level  # failed to reclaim below
        return bar.close < self.broken_level  # failed to reclaim above


def check_reversal_entry(
    bos_direction: Optional[str],
    retested: bool,
    prev_bar: Bar,
    curr_bar: Bar,
    cfg: EmaFibReversalConfig,
) -> Optional[str]:
    """Change-of-character entry: BOS + failed retest + reversal candle.
    Lower win-rate / higher R:R than pullback entries by design -- gate with
    reversal_min_reward_risk, not the pullback minimum.
    """
    if not cfg.enable_reversal_entries or not retested:
        return None
    if bos_direction == "bullish" and (
        is_bullish_engulfing(prev_bar, curr_bar) or is_hammer(curr_bar, cfg.candle)
    ):
        return LONG
    if bos_direction == "bearish" and (
        is_bearish_engulfing(prev_bar, curr_bar) or is_shooting_star(curr_bar, cfg.candle)
    ):
        return SHORT
    return None


def check_exit(
    position: Position,
    curr_bar: Bar,
    cfg: EmaFibReversalConfig,
    opposing_signal: bool,
) -> ExitDecision:
    """Priority: stop > opposing reversal > full target > partial target >
    hold. Fill prices are the trigger level itself (a backtest
    simplification -- see docs/SPEC.md for slippage handling). Forced
    session-end flattening is the engine's job (an account-level session
    policy), not this strategy's -- see BacktestEngine.run.
    """
    is_long = position.direction == LONG

    stop_hit = curr_bar.low <= position.stop_price if is_long else curr_bar.high >= position.stop_price
    if stop_hit:
        return ExitDecision(action="stop", fill_price=position.stop_price, fraction=1.0)

    if opposing_signal:
        return ExitDecision(action="reversal_flatten", fill_price=curr_bar.close, fraction=1.0)

    if position.partial_taken:
        target2_hit = curr_bar.high >= position.target_2 if is_long else curr_bar.low <= position.target_2
        if target2_hit:
            return ExitDecision(action="full_target", fill_price=position.target_2, fraction=1.0)
        return ExitDecision(action="none")

    target1_hit = curr_bar.high >= position.target_1 if is_long else curr_bar.low <= position.target_1
    if target1_hit:
        return ExitDecision(
            action="partial_target",
            fill_price=position.target_1,
            fraction=cfg.exit.partial_exit_fraction,
            new_stop=position.entry_price,
        )
    return ExitDecision(action="none")


# ---------------------------------------------------------------- stateful Strategy adapter

class EmaFibReversalStrategy(Strategy):
    def __init__(self, cfg: EmaFibReversalConfig, account: AccountConfig):
        self.cfg = cfg
        self.account = account
        self.tick_size = account.contract.tick_size

        self._pending_reversal: Optional[ReversalSetup] = None
        self._pending_reversal_since = -1

        # Populated by precompute_batch()
        self.bars_bias: Optional[pd.DataFrame] = None
        self.bars_entry: Optional[pd.DataFrame] = None
        self.bias_inputs = None
        self.bias_series: Optional[pd.Series] = None
        self.bias_persistence: Optional[pd.Series] = None
        self.entry_swings: Optional[pd.DataFrame] = None
        self.entry_ema_mid: Optional[pd.Series] = None
        self.entry_atr: Optional[pd.Series] = None
        self.entry_atr_percentile: Optional[pd.Series] = None
        self.entry_vwap: Optional[pd.Series] = None

    # ---- Strategy interface ----

    @property
    def name(self) -> str:
        return "ema_fib_reversal"

    @property
    def timeframes(self) -> dict:
        return {
            "bias": TimeframeSpec(self.cfg.bias_timeframe),
            "entry": TimeframeSpec(self.cfg.entry_timeframe, warmup_bars=max(self.cfg.ema.slow, 14)),
        }

    @property
    def driving_timeframe(self) -> str:
        return "entry"

    def precompute_batch(self, full_history: dict) -> None:
        self.bars_bias = full_history["bias"]
        self.bars_entry = full_history["entry"]

        self.bias_inputs = precompute_bias_inputs(self.bars_bias, self.cfg.ema, self.cfg.swing)
        # Bias only ever depends on bars up to its own position, so
        # precomputing the full series once is still fully causal -- it's
        # just a vectorized version of calling get_bias() at every position.
        self.bias_series = pd.Series(
            [
                get_bias(self.bars_bias, self.bias_inputs, pos, self.cfg.ema, self.cfg.swing)
                for pos in range(len(self.bars_bias))
            ],
            index=self.bars_bias.index,
        )
        self.bias_persistence = consecutive_run_length(self.bias_series)

        self.entry_swings = compute_swings(self.bars_entry, lookback=self.cfg.swing.lookback)
        self.entry_ema_mid = ema(self.bars_entry["close"], self.cfg.ema.mid)
        self.entry_atr = atr(self.bars_entry, period=14)
        self.entry_atr_percentile = rolling_percentile(self.entry_atr, lookback=VOLATILITY_LOOKBACK_BARS)
        self.entry_vwap = session_vwap(self.bars_entry, tz=self.account.session.timezone)

    def on_bar(self, snapshot: MarketSnapshot) -> None:
        # All of this strategy's state updates (pending reversal tracking)
        # happen inside check_entry, matching this strategy's original
        # design: they only ever advanced while flat and eligible to enter.
        return

    def check_entry(self, snapshot: MarketSnapshot) -> Optional[EntrySignal]:
        j, bias_pos, bias, bar, prev_bar = self._unpack(snapshot)

        signal = None
        if self.cfg.enable_pullback_entries:
            signal = self._try_open_pullback(j, bias_pos, bias, bar, prev_bar)
        if signal is None and self.cfg.enable_reversal_entries:
            signal = self._manage_reversal(j, bias_pos, bias, bar, prev_bar)
        return signal

    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        j, bias_pos, bias, bar, prev_bar = self._unpack(snapshot)
        opp_signal, _ = self._pullback_signal(j, bias, bar, prev_bar)
        opposing = opp_signal is not None and opp_signal != position.direction
        return check_exit(position, bar, self.cfg, opposing_signal=opposing)

    def diagnostic_dimensions(self) -> list:
        return ["bias", "volatility_regime", "trend_regime"]

    # ---- private helpers ----

    def _unpack(self, snapshot: MarketSnapshot):
        entry_view = snapshot.timeframes["entry"]
        bias_view = snapshot.timeframes["bias"]
        j = entry_view.pos
        bias_pos = bias_view.pos
        bias = "neutral" if bias_pos < 0 else self.bias_series.iloc[bias_pos]
        bar = entry_view.bar(0)
        prev_bar = entry_view.bar(1) if j > 0 else bar
        return j, bias_pos, bias, bar, prev_bar

    def _pullback_signal(self, j: int, bias: str, bar: Bar, prev_bar: Bar):
        """Returns (direction_or_None, fib_levels_used_or_None)."""
        if bias not in ("bullish", "bearish"):
            return None, None
        impulse = find_impulse_leg(self.bars_entry, self.entry_swings, j, self.cfg.swing.lookback, bias)
        if impulse is None:
            return None, None
        fib_levels = get_fib_levels(*impulse, self.cfg.fib)
        signal_dir = check_pullback_entry(
            bias, bar.close, fib_levels, self.entry_ema_mid.iloc[j], self.entry_atr.iloc[j], prev_bar, bar, self.cfg
        )
        return signal_dir, fib_levels

    def _build_context(self, j: int, bias_pos: int, bias: str, bar: Bar) -> dict:
        """Diagnostic context for trade-log enrichment -- see
        mnq_system/regime.py. Purely descriptive; none of this feeds back
        into any entry/exit decision.
        """
        atr_val = self.entry_atr.iloc[j]
        vwap_val = self.entry_vwap.iloc[j]
        atr_pct_rank = self.entry_atr_percentile.iloc[j]

        ema_slope_pct = None
        slope_lookback = self.cfg.ema.slope_lookback
        if bias_pos >= slope_lookback:
            ema_slow_now = self.bias_inputs.ema_slow.iloc[bias_pos]
            ema_slow_prior = self.bias_inputs.ema_slow.iloc[bias_pos - slope_lookback]
            if pd.notna(ema_slow_now) and pd.notna(ema_slow_prior) and ema_slow_prior != 0:
                ema_slope_pct = float((ema_slow_now - ema_slow_prior) / ema_slow_prior)

        persistence = int(self.bias_persistence.iloc[bias_pos]) if bias_pos >= 0 else None
        trend_regime = (
            "trending"
            if bias != "neutral" and persistence is not None and persistence >= TREND_PERSISTENCE_BARS_THRESHOLD
            else "choppy"
        )

        vwap_distance_atr = None
        if pd.notna(vwap_val) and pd.notna(atr_val) and atr_val > 0:
            vwap_distance_atr = float((bar.close - vwap_val) / atr_val)

        return {
            "bias": bias,
            "atr": float(atr_val) if pd.notna(atr_val) else None,
            "atr_pct_of_price": float(atr_val / bar.close) if pd.notna(atr_val) and bar.close else None,
            "volatility_regime": bucket_percentile(atr_pct_rank),
            "bias_ema_slope_pct": ema_slope_pct,
            "bias_persistence_bars": persistence,
            "trend_regime": trend_regime,
            "vwap_distance_atr": vwap_distance_atr,
        }

    def _try_open_pullback(self, j, bias_pos, bias, bar, prev_bar) -> Optional[EntrySignal]:
        signal_dir, fib_levels = self._pullback_signal(j, bias, bar, prev_bar)
        if signal_dir is None:
            return None

        swing_kind = "low" if signal_dir == "long" else "high"
        swing = latest_confirmed_swing(self.bars_entry, self.entry_swings, j, self.cfg.swing.lookback, swing_kind)
        if swing is None:
            return None
        stop = get_stop(signal_dir, swing.price, self.tick_size, self.cfg.exit.stop_buffer_ticks)
        target_1, target_2 = fib_levels.ext_target_1, fib_levels.ext_target_2
        if not meets_min_reward_risk(bar.close, stop, target_1, self.cfg.exit.min_reward_risk):
            return None

        context = self._build_context(j, bias_pos, bias, bar)
        return EntrySignal(
            direction=signal_dir, setup_type="pullback", entry_price=bar.close, stop_price=stop,
            targets=[target_1, target_2], context=context,
        )

    def _manage_reversal(self, j, bias_pos, bias, bar, prev_bar) -> Optional[EntrySignal]:
        last_high = latest_confirmed_swing(self.bars_entry, self.entry_swings, j, self.cfg.swing.lookback, "high")
        last_low = latest_confirmed_swing(self.bars_entry, self.entry_swings, j, self.cfg.swing.lookback, "low")
        bos = detect_bos(bar.close, last_high.price if last_high else None, last_low.price if last_low else None)

        pending = self._pending_reversal
        since = self._pending_reversal_since
        if pending is not None and (j - since) > REVERSAL_SETUP_TIMEOUT_BARS:
            pending = None

        if pending is None:
            if bos is None:
                self._pending_reversal, self._pending_reversal_since = None, -1
                return None
            broken_level = last_high.price if bos == "bullish" else last_low.price
            atr_val = self.entry_atr.iloc[j]
            tolerance = atr_val * 0.25 if pd.notna(atr_val) else self.tick_size * 4
            self._pending_reversal = ReversalSetup(direction=bos, broken_level=broken_level, retest_tolerance=tolerance)
            self._pending_reversal_since = j
            return None

        retested = pending.check_retest(bar)
        signal_dir = check_reversal_entry(pending.direction, retested, prev_bar, bar, self.cfg)
        if signal_dir is None:
            self._pending_reversal, self._pending_reversal_since = pending, since
            return None

        # A genuine change-of-character requires the break to run counter to
        # (or the bias to be undecided about) the prevailing bias -- if bias
        # already agrees with the break direction this is just continuation,
        # not a reversal, so keep watching rather than fire here.
        if bias == pending.direction:
            self._pending_reversal, self._pending_reversal_since = pending, since
            return None

        # Empirically weak bucket under test -- see EmaFibReversalConfig docstring.
        if self.cfg.filter_reversal_long_vs_bearish_bias and pending.direction == "bullish" and bias == "bearish":
            self._pending_reversal, self._pending_reversal_since = pending, since
            return None

        stop = get_stop(signal_dir, pending.broken_level, self.tick_size, self.cfg.exit.stop_buffer_ticks)
        risk = abs(bar.close - stop)

        # Floor the stop distance so a retest that closes very near the
        # broken level can't produce a near-zero-risk trade -- which the
        # position sizer would otherwise "fix" with an unrealistic contract count.
        atr_val = self.entry_atr.iloc[j]
        min_risk = atr_val * self.cfg.exit.reversal_min_stop_atr_mult if pd.notna(atr_val) else 0.0
        if risk < min_risk:
            risk = min_risk
            sign = 1 if signal_dir == "long" else -1
            stop = bar.close - risk * sign

        rr = self.cfg.exit.reversal_min_reward_risk
        sign = 1 if signal_dir == "long" else -1
        target_1 = bar.close + risk * rr * sign
        target_2 = bar.close + risk * (rr + 1) * sign
        if not meets_min_reward_risk(bar.close, stop, target_1, rr):
            self._pending_reversal, self._pending_reversal_since = None, -1
            return None

        context = self._build_context(j, bias_pos, bias, bar)
        context["break_direction"] = pending.direction
        self._pending_reversal, self._pending_reversal_since = None, -1
        return EntrySignal(
            direction=signal_dir, setup_type="reversal", entry_price=bar.close, stop_price=stop,
            targets=[target_1, target_2], context=context,
        )
