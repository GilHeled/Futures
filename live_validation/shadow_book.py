"""
Shadow position book (no broker, no orders). Holds at most one shadow
position per instrument, opened on an off-hours h10 signal, exited by the
frozen gap-aware dynamic_ev rule. At entry and exit it records TWO fills:

  - expected_fill : the backtest convention (bar close +/- assumed
    slippage), the number our edge estimate was built on.
  - crossing_fill : the price implied by crossing the REAL quoted spread
    at that instant (buy@ask / sell@bid). Under live data this is the
    "actual" execution price; realized slippage = crossing - expected.

Stops are gap-aware: if a bar's open has already gapped past the stop, the
fill is the open (worse), not the stop price. EV-reversal exits fill at the
next bar's open (one bar after the close that revealed the reversal --
causal, matching the backtest).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def apply_slippage(price: float, direction: str, is_entry: bool, tick_size: float, slippage_ticks: float) -> float:
    """Adverse-fill convention identical to BacktestEngine._apply_slippage."""
    if slippage_ticks <= 0:
        return price
    adverse = slippage_ticks * tick_size
    worse_when_long = is_entry
    if direction == "long":
        return price + adverse if worse_when_long else price - adverse
    return price - adverse if worse_when_long else price + adverse


STOP_ATR_MULT = 1.5
MAX_HOLD_BARS = 500  # safety cap mirroring the offline sim's MAX_FORWARD_BARS


@dataclass
class ShadowPosition:
    entry_ts: object
    direction: int          # +1 long, -1 short
    entry_close: float      # raw bar close at signal
    stop_price: float
    entry_expected: float
    entry_crossing: Optional[float]
    entry_ev: Optional[float]
    entry_crossing_valid: bool = True
    pending_exit: bool = False
    bars_held: int = 0


class ShadowBook:
    """Single-instrument shadow book. Feed it every bar via on_bar(); it
    emits a completed-trade dict on exit (else None)."""

    def __init__(self, symbol: str, bundle):
        self.symbol = symbol
        self.bundle = bundle
        self.tick = bundle.tick_size
        self.pv = bundle.point_value
        self.slip = bundle.fill_slippage_ticks  # realistic execution slippage for fills
        self.commission = bundle.commission
        self.pos: Optional[ShadowPosition] = None

    def _cross_price(self, direction: str, is_entry: bool, price: float, quote: Optional[dict]) -> float:
        """Crossing fill: cross the real spread (buy@ask / sell@bid). We BUY
        to open a long or close a short, SELL to open a short or close a
        long. If no quote is available (quoteless historical replay), fall
        back to the exact expected-fill convention so replay's
        crossing==expected (sanity path)."""
        is_long = direction == "long"
        side_buy = (is_entry and is_long) or (not is_entry and not is_long)
        if quote and quote.get("bid") is not None and quote.get("ask") is not None:
            return quote["ask"] if side_buy else quote["bid"]
        return apply_slippage(price, direction, is_entry, self.tick, self.slip)

    def on_bar(self, ts, ohlc: dict, ev: Optional[float], direction_signal: int,
               off_hours: bool, session_ending: bool, quote: Optional[dict] = None,
               quote_valid: bool = True) -> Optional[dict]:
        """`quote_valid=False` means the only available quote was from a
        DIFFERENT underlying contract than this bar (a rollover mismatch,
        detected upstream). In that case the crossing fill is NOT fabricated
        -- it is recorded as None/invalid so the observation is excluded from
        the execution (slippage/crossing-R) statistics, rather than injecting
        an artificial zero-slippage sample. (Quoteless historical replay
        keeps quote_valid=True with quote=None -> expected-fill fallback, the
        intended sanity path.)"""
        high, low, open_, close = ohlc["high"], ohlc["low"], ohlc["open"], ohlc["close"]

        # ---- manage an open position first ----
        # NB: this is the OVERNIGHT off-hours strategy exactly as the edge was
        # measured -- it HOLDS THROUGH the session boundary to its own
        # gap-aware dynamic_ev exit. There is deliberately NO session-flatten
        # here (that belongs to the RTH strategy, not this one); flattening
        # overnight positions at the session boundary would destroy the very
        # hold this phase validates.
        if self.pos is not None:
            p = self.pos
            p.bars_held += 1
            is_long = p.direction == 1
            exit_raw = None
            reason = None
            stop_hit = low <= p.stop_price if is_long else high >= p.stop_price
            if stop_hit:  # gap-aware: worse of stop and this bar's open
                exit_raw = min(p.stop_price, open_) if is_long else max(p.stop_price, open_)
                reason = "stop"
            elif p.pending_exit:
                exit_raw, reason = open_, "ev_reversal"
            elif p.bars_held >= MAX_HOLD_BARS:
                exit_raw, reason = close, "max_hold"
            elif ev is not None:
                favorable = ev > 0 if is_long else ev < 0
                if not favorable:
                    p.pending_exit = True

            if exit_raw is not None:
                d = "long" if is_long else "short"
                exit_expected = apply_slippage(exit_raw, d, False, self.tick, self.slip)
                exit_crossing = self._cross_price(d, False, exit_raw, quote) if quote_valid else None
                # execution observation is valid only if BOTH fills came from a
                # quote on the same contract as their bar; else crossing R and
                # slippage are None and the trade is excluded from execution stats.
                execution_valid = bool(p.entry_crossing_valid and quote_valid)
                risk = abs(p.entry_expected - p.stop_price) * self.pv
                def r(entry_fill, exit_fill):
                    if entry_fill is None or exit_fill is None or risk <= 0:
                        return None
                    return ((exit_fill - entry_fill) * p.direction * self.pv - self.commission) / risk
                sgn = 1 if is_long else -1
                entry_slip = ((p.entry_crossing - p.entry_expected) / self.tick * sgn) if p.entry_crossing_valid else None
                exit_slip = ((exit_expected - exit_crossing) / self.tick * sgn) if quote_valid else None
                trade = {
                    "symbol": self.symbol, "entry_ts": str(p.entry_ts), "exit_ts": str(ts),
                    "direction": "long" if is_long else "short", "exit_reason": reason,
                    "entry_ev": p.entry_ev,
                    "entry_expected": p.entry_expected, "entry_crossing": p.entry_crossing,
                    "exit_expected": exit_expected, "exit_crossing": exit_crossing,
                    "r_expected": r(p.entry_expected, exit_expected),
                    "r_crossing": r(p.entry_crossing, exit_crossing),
                    "entry_slippage_ticks": entry_slip,
                    "exit_slippage_ticks": exit_slip,
                    "entry_crossing_valid": p.entry_crossing_valid,
                    "exit_crossing_valid": bool(quote_valid),
                    "execution_valid": execution_valid,
                    "bundle_version": self.bundle.meta.get("bundle_version"),
                    "model_version": self.bundle.meta.get("model_version"),
                }
                self.pos = None
                return trade
            return None

        # ---- flat: open on an off-hours directional signal ----
        if direction_signal != 0 and off_hours and not session_ending:
            sign = int(direction_signal)
            d = "long" if sign == 1 else "short"
            atr_val = ohlc.get("atr")
            if atr_val is None or atr_val <= 0:
                return None
            stop = close - STOP_ATR_MULT * atr_val * sign
            entry_expected = apply_slippage(close, d, True, self.tick, self.slip)
            entry_crossing = self._cross_price(d, True, close, quote) if quote_valid else None
            self.pos = ShadowPosition(
                entry_ts=ts, direction=sign, entry_close=close, stop_price=stop,
                entry_expected=entry_expected, entry_crossing=entry_crossing, entry_ev=ev,
                entry_crossing_valid=bool(quote_valid),
            )
        return None
