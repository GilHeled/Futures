"""
Three-outcome expected value, in DOLLARS, consistent units.

Given the model's class probabilities P(UP)/P(DOWN)/P(TIMEOUT), the barrier
size k·ATR (price), the single causal timeout return `tret` (signed price
return entry→time-barrier for a LONG; the short timeout outcome is −tret),
the instrument point value, and the round-trip cost in dollars:

  EV_long  = point_value·[ P(UP)·(k·ATR) + P(DOWN)·(−k·ATR) + P(TIMEOUT)·tret ] − rt_cost
  EV_short = point_value·[ P(DOWN)·(k·ATR) + P(UP)·(−k·ATR) + P(TIMEOUT)·(−tret) ] − rt_cost

The decision rule (see the frozen plan): alert on the higher-EV side, only
if that EV > 0. `tret_zero=True` implements the pre-registered sensitivity
gate (timeout PnL forced to 0 before costs).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EV:
    ev_long: float
    ev_short: float

    @property
    def best_side(self):
        if self.ev_long <= 0 and self.ev_short <= 0:
            return None
        return "long" if self.ev_long >= self.ev_short else "short"

    @property
    def best_ev(self) -> float:
        return max(self.ev_long, self.ev_short)


def expected_value(p_up: float, p_down: float, p_timeout: float, k: float, atr: float,
                   tret: float, point_value: float, rt_cost: float, tret_zero: bool = False) -> EV:
    barrier = k * atr                      # price distance of the target/stop
    t = 0.0 if tret_zero else tret         # signed price return at timeout (long)
    ev_long = point_value * (p_up * barrier + p_down * (-barrier) + p_timeout * t) - rt_cost
    ev_short = point_value * (p_down * barrier + p_up * (-barrier) + p_timeout * (-t)) - rt_cost
    return EV(ev_long=ev_long, ev_short=ev_short)


def round_trip_cost(commission_per_rt: float, spread_ticks: float, slippage_ticks: float,
                    tick_size: float, point_value: float) -> float:
    """Dollar round-trip cost: commission + (spread + 2·slippage) crossing cost."""
    return commission_per_rt + (spread_ticks + 2 * slippage_ticks) * tick_size * point_value
