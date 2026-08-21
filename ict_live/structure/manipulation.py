"""Manipulation = liquidity SWEEP (EXPERIMENTAL; nothing frozen).

Objective detector: an active/structural ERL pool is RAIDED when a later bar's wick trades through
the resting level and then CLOSES BACK inside (rejection). A wick-through that CLOSES BEYOND the
level is acceptance / a break of structure, NOT manipulation, and is excluded here. Buy-side
liquidity (above a structural high) raided + rejected → bearish manipulation; sell-side (below a
structural low) raided + rejected → bullish manipulation. The wick extreme is the MANIPULATION
EXTREME (later used for stop placement / geometry).

Every Sweep exposes its `reason` and `depends_on` (the ERL pool, the source swing, the sweeping
bar) so a recommendation traces back to raw structure. Causal: uses only the sweeping bar.

Provisional interpretation (marked): "rejection" = close back inside the level on the sweeping
bar. Whether a multi-bar reclaim should also count is left for the visual/course evidence; not
frozen. This detector produces candidates only — context/state-machine decides acceptance later.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ict_live.market.bar import Bar
from ict_live.structure import ids
from ict_live.structure.swing_liquidity import SwingPool


@dataclass(frozen=True)
class Sweep:
    id: str
    direction: str            # "bearish" (buy-side raided) | "bullish" (sell-side raided)
    pool_price: float         # the resting liquidity level that was raided
    extreme: float            # manipulation extreme (wick beyond the level)
    bar_index: int            # bar that did the raid
    time: datetime
    pool_index: int = -1      # bar index of the source structural swing/pool (for ranking lookup)
    close: float = 0.0        # sweeping bar's close (rejection strength)
    reason: str = ""
    depends_on: tuple[str, ...] = field(default_factory=tuple)


def detect_sweeps(pools: list[SwingPool], bars: list[Bar]) -> list[Sweep]:
    out: list[Sweep] = []
    for p in pools:
        if not p.swept or p.swept_index is None:
            continue
        j = p.swept_index
        b = bars[j]
        if p.kind == "high":
            rejected = b.close < p.price          # closed back below the raided high
            direction, extreme = "bearish", b.high
        else:
            rejected = b.close > p.price          # closed back above the raided low
            direction, extreme = "bullish", b.low
        if not rejected:
            continue                              # closed beyond -> acceptance/BOS, not manipulation
        side = "buy-side (BSL)" if p.kind == "high" else "sell-side (SSL)"
        why = (f"{side} liquidity at {p.price:g} (ERL from structural {p.kind}) raided — wick to "
               f"{extreme:g} at bar {j} ({b.open_time.isoformat()}), then rejected "
               f"(close {b.close:g} back {'below' if p.kind == 'high' else 'above'} {p.price:g}) "
               f"→ {direction} manipulation. Manipulation extreme = {extreme:g}.")
        out.append(Sweep(
            id=ids.sweep_id(p.index, p.kind, j),
            direction=direction, pool_price=float(p.price), extreme=float(extreme),
            bar_index=j, time=b.open_time, pool_index=p.index, close=float(b.close), reason=why,
            depends_on=(ids.pool_id(p), ids.bar_id(j)),
        ))
    return out
