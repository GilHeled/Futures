"""ICT v2 — the pipeline as THREE EXPLICIT STAGES, exactly like the course:

    HTF context   →   MTF setup   →   LTF execution

Each stage runs on its own timeframe. This first iteration reuses the frozen v1 engine
(`ict_live.engine.pipeline.analyze`) per timeframe and exposes that layer's objects; the cross-
timeframe gating (bias/zone/liquidity confluence) is layered on in later increments. v1 is imported
READ-ONLY and never modified.

    from ict_v2 import pipeline as v2
    state = v2.analyze_mtf(htf_bars, mtf_bars, ltf_bars, htf="4H", mtf="15m", ltf="1m")
    print(state.describe())
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ict_live.engine import pipeline as v1        # frozen v1 engine, read-only


# ---- the three layers -------------------------------------------------------------------------
@dataclass
class HTFContext:
    """Stage 1 — the higher-timeframe context: bias, dealing range, and the liquidity draw."""
    tf: str
    bias: str                              # "long" | "short" | "neutral"
    dealing_range: object = None           # v1 DealingRange (premium/discount/EQ) or None
    liquidity: list = field(default_factory=list)   # active ERL pools = the draw on liquidity

    def zone(self, price: float) -> Optional[str]:
        """premium / discount / equilibrium of `price` within the HTF dealing range (None if no range)."""
        return self.dealing_range.zone_of(price) if self.dealing_range is not None else None


@dataclass
class MTFSetup:
    """Stage 2 — the intermediate-timeframe setup: manipulation, displacement, MSS."""
    tf: str
    sweeps: list = field(default_factory=list)         # ranked manipulation (liquidity raids)
    displacements: list = field(default_factory=list)
    mss: list = field(default_factory=list)


@dataclass
class LTFExecution:
    """Stage 3 — the lower-timeframe execution: entry FVGs + the executable setup."""
    tf: str
    fvgs: list = field(default_factory=list)           # ranked entry FVGs
    setup: object = None                               # executable setup (entry/stop/target) or None
    recommendation: object = None                      # v1 Recommendation (decision + reason)


@dataclass
class MTFState:
    """The full three-stage result."""
    context: HTFContext
    setup: MTFSetup
    execution: LTFExecution

    def describe(self) -> str:
        c, s, e = self.context, self.setup, self.execution
        dr = c.dealing_range
        drs = f"{dr.low:g}-{dr.high:g} (CE {dr.ce:g}, {dr.direction})" if dr else "none"
        su = e.setup
        if su:
            tgt = f"{su.target:g}" if su.target is not None else "—"
            sus = f"{su.direction} entry {su.entry:g} stop {su.stop:g} target {tgt} RR {su.rr:g}"
        else:
            sus = "none"
        return (
            f"[1] HTF CONTEXT  ({c.tf}): bias={c.bias}  dealing_range={drs}  liquidity_draws={len(c.liquidity)}\n"
            f"[2] MTF SETUP    ({s.tf}): sweeps={len(s.sweeps)}  displacements={len(s.displacements)}  mss={len(s.mss)}\n"
            f"[3] LTF EXECUTION({e.tf}): fvgs={len(e.fvgs)}  setup={sus}  decision={getattr(e.recommendation,'decision',None)}"
        )


# ---- the three stages -------------------------------------------------------------------------
def htf_context(bars, tf: str) -> HTFContext:
    """Stage 1: run the engine on the HTF and keep the context layer (bias + dealing range + draw)."""
    ms = v1.analyze(bars, tf)
    dr = ms.ranges[0] if ms.ranges else None
    bias = "long" if (dr and dr.direction == "up") else "short" if (dr and dr.direction == "down") else "neutral"
    return HTFContext(tf=tf, bias=bias, dealing_range=dr, liquidity=list(ms.active_erl))


def mtf_setup(bars, tf: str, context: HTFContext) -> MTFSetup:
    """Stage 2: run the engine on the MTF and keep the setup layer (manipulation/displacement/MSS)."""
    ms = v1.analyze(bars, tf)
    return MTFSetup(tf=tf, sweeps=list(ms.ranked_sweeps), displacements=list(ms.ranked_displacements),
                    mss=list(ms.ranked_mss))


def ltf_execution(bars, tf: str, setup: MTFSetup, context: HTFContext) -> LTFExecution:
    """Stage 3: run the engine on the LTF and keep the execution layer (entry FVG + setup)."""
    ms = v1.analyze(bars, tf)
    return LTFExecution(tf=tf, fvgs=list(ms.ranked_fvgs), setup=ms.recommendation.setup,
                        recommendation=ms.recommendation)


def analyze_mtf(htf_bars, mtf_bars, ltf_bars, *, htf: str = "4H", mtf: str = "15m",
                ltf: str = "1m") -> MTFState:
    """Run the three explicit stages in order and return the combined state."""
    ctx = htf_context(htf_bars, htf)
    stp = mtf_setup(mtf_bars, mtf, ctx)
    exe = ltf_execution(ltf_bars, ltf, stp, ctx)
    return MTFState(context=ctx, setup=stp, execution=exe)


# ---- runnable demo ----------------------------------------------------------------------------
def _demo_bars(n, tf, minutes, seed):
    from datetime import datetime, timedelta, timezone
    from ict_live.market.bar import Bar
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 21) - 10) * 1.5
        ot = t0 + timedelta(minutes=minutes * i)
        bars.append(Bar(tf, ot, ot + timedelta(minutes=minutes), o, max(o, c) + (x % 7),
                        min(o, c) - (x % 5), c, 100.0))
        px = c
    return bars


def main() -> None:
    htf = _demo_bars(260, "4H", 240, 7)
    mtf = _demo_bars(260, "15m", 15, 11)
    ltf = _demo_bars(260, "1m", 1, 23)
    state = analyze_mtf(htf, mtf, ltf, htf="4H", mtf="15m", ltf="1m")
    print("ICT v2 — three-stage pipeline (demo data)\n")
    print(state.describe())


if __name__ == "__main__":
    main()
