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
from ict_v2 import align


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
class GatedSetup:
    """An MTF setup that PASSED the HTF gate, plus the HTF liquidity objective it targets."""
    setup: object                                      # v1 Setup (direction/entry/stop/target)
    objective: object = None                           # HTF liquidity pool = the draw (target)


@dataclass
class MTFSetup:
    """Stage 2 — the intermediate-timeframe setup: manipulation, displacement, MSS, and the setups
    that survive the HTF gate."""
    tf: str
    sweeps: list = field(default_factory=list)         # ranked manipulation (liquidity raids)
    displacements: list = field(default_factory=list)
    mss: list = field(default_factory=list)
    candidates: list = field(default_factory=list)     # all MTF setups (pre-gate)
    gated: list = field(default_factory=list)          # list[GatedSetup] that passed the HTF gate


@dataclass
class Executable:
    """A gated setup turned into an executable ticket on the LTF (target = HTF liquidity draw)."""
    direction: str
    entry: float
    stop: float
    target: Optional[float]
    ltf_confirmed: bool                                # an LTF entry FVG is present
    objective: object = None


@dataclass
class LTFExecution:
    """Stage 3 — execution, operating ONLY on MTF setups that passed the HTF gate."""
    tf: str
    fvgs: list = field(default_factory=list)           # ranked LTF entry FVGs
    executables: list = field(default_factory=list)    # list[Executable] (only for gated setups)
    decision: str = "NO-TRADE"


@dataclass
class MTFState:
    """The full three-stage result. `execution` is the current (finest) execution; `executions`
    holds every exec-TF's state (e.g. 15m and 1m) for the step-down view."""
    context: HTFContext
    setup: MTFSetup
    execution: LTFExecution
    executions: dict = field(default_factory=dict)

    def describe(self) -> str:
        c, s, e = self.context, self.setup, self.execution
        dr = c.dealing_range
        drs = f"{dr.low:g}-{dr.high:g} (CE {dr.ce:g}, {dr.direction})" if dr else "none"
        ex = e.executables[0] if e.executables else None
        if ex:
            tgt = f"{ex.target:g}" if ex.target is not None else "—"
            exs = f"{ex.direction} entry {ex.entry:g} stop {ex.stop:g} target {tgt} (LTF-confirmed={ex.ltf_confirmed})"
        else:
            exs = "none"
        return (
            f"[1] HTF CONTEXT  ({c.tf}): bias={c.bias}  dealing_range={drs}  liquidity_draws={len(c.liquidity)}\n"
            f"[2] MTF SETUP    ({s.tf}): sweeps={len(s.sweeps)} displ={len(s.displacements)} mss={len(s.mss)} "
            f"| candidates={len(s.candidates)} -> GATED(passed HTF)={len(s.gated)}\n"
            f"[3] LTF EXECUTION({e.tf}): fvgs={len(e.fvgs)}  executables={len(e.executables)}  "
            f"decision={e.decision}  {exs}"
        )


# ---- the three stages -------------------------------------------------------------------------
def htf_context(bars, tf: str) -> HTFContext:
    """Stage 1: run the engine on the HTF and keep the context layer (bias + dealing range + draw)."""
    ms = v1.analyze(bars, tf)
    dr = ms.ranges[0] if ms.ranges else None
    bias = "long" if (dr and dr.direction == "up") else "short" if (dr and dr.direction == "down") else "neutral"
    return HTFContext(tf=tf, bias=bias, dealing_range=dr, liquidity=list(ms.active_erl))


def mtf_setup(bars, tf: str, context: HTFContext) -> MTFSetup:
    """Stage 2: run the engine on the MTF, then GATE each setup by the HTF context (bias +
    premium/discount + liquidity objective). Only setups that pass are carried forward."""
    ms = v1.analyze(bars, tf)
    # only consider setups v1 itself deems ACTIONABLE — non-actionable ones are rejected by v1
    # (mitigated FVG, RR too low, degenerate geometry) and must never be executed
    candidates = [r.item for r in ms.ranked_setups if getattr(r.item, "actionable", False)]
    gated = []
    for su in candidates:
        passed, _reasons, objective = align.gate_setup(su, context)
        if passed:
            gated.append(GatedSetup(setup=su, objective=objective))
    return MTFSetup(tf=tf, sweeps=list(ms.ranked_sweeps), displacements=list(ms.ranked_displacements),
                    mss=list(ms.ranked_mss), candidates=candidates, gated=gated)


def ltf_execution(bars, tf: str, setup: MTFSetup, context: HTFContext) -> LTFExecution:
    """Stage 3: execution runs ONLY on MTF setups that passed the HTF gate. For each, produce an
    executable (target = the HTF liquidity draw), and note whether the LTF shows an entry FVG."""
    if not setup.gated:
        return LTFExecution(tf=tf, fvgs=[], executables=[], decision="NO-TRADE (no HTF-gated setup)")
    ms = v1.analyze(bars, tf)
    fvgs = list(ms.ranked_fvgs)
    executables = []
    for g in setup.gated:
        su, obj = g.setup, g.objective
        executables.append(Executable(direction=su.direction, entry=su.entry, stop=su.stop,
                                       target=(obj.price if obj is not None else su.target),
                                       ltf_confirmed=bool(fvgs), objective=obj))
    top = executables[0]
    decision = "LONG" if top.direction == "long" else "SHORT"
    return LTFExecution(tf=tf, fvgs=fvgs, executables=executables, decision=decision)


def analyze_mtf(htf_bars, mtf_bars, ltf_bars, *, htf: str = "4H", mtf: str = "15m",
                ltf: str = "1m") -> MTFState:
    """Run the three explicit stages in order and return the combined state."""
    ctx = htf_context(htf_bars, htf)
    stp = mtf_setup(mtf_bars, mtf, ctx)
    exe = ltf_execution(ltf_bars, ltf, stp, ctx)
    return MTFState(context=ctx, setup=stp, execution=exe)


# ---- resampling + demo ------------------------------------------------------------------------
def resample(bars, factor: int, tf: str):
    """Aggregate every `factor` consecutive bars into one `tf` bar (o=first, h=max, l=min, c=last).
    Lets the three timeframes be derived from ONE underlying series so their structure is consistent."""
    from ict_live.market.bar import Bar
    out = []
    for i in range(0, len(bars), factor):
        ch = bars[i:i + factor]
        if not ch:
            continue
        out.append(Bar(tf, ch[0].open_time, ch[-1].close_time, ch[0].open,
                       max(b.high for b in ch), min(b.low for b in ch), ch[-1].close,
                       sum(b.volume for b in ch)))
    return out


def _base_1m(n, seed):
    from datetime import datetime, timedelta, timezone
    from ict_live.market.bar import Bar
    bars, px, x = [], 20000.0, seed
    t0 = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)
    for i in range(n):
        x = (1103515245 * x + 12345) % (2 ** 31)
        o = px
        c = px + ((x % 25) - 12) * 0.6
        ot = t0 + timedelta(minutes=i)
        bars.append(Bar("1m", ot, ot + timedelta(minutes=1), o, max(o, c) + (x % 5) * 0.4,
                        min(o, c) - (x % 4) * 0.4, c, 100.0))
        px = c
    return bars


def demo_state(seed=7):
    """Build HTF/MTF/LTF from one 1m base (so they're consistent) and run the three stages."""
    base = _base_1m(20000, seed)                 # ~13 sessions of 1m
    htf = resample(base, 240, "4H")
    mtf = resample(base, 15, "15m")
    ltf = base[-400:]                            # recent 1m for execution
    return analyze_mtf(htf, mtf, ltf, htf="4H", mtf="15m", ltf="1m")


def main() -> None:
    # search a few seeds for one that actually passes the HTF gate, to show the full path firing
    state = None
    for seed in (7, 11, 23, 42, 101, 202, 303):
        st = demo_state(seed)
        if st.setup.gated:
            state = st
            print(f"ICT v2 — three-stage pipeline (correlated demo data, seed={seed})\n")
            break
    if state is None:
        state = demo_state(7)
        print("ICT v2 — three-stage pipeline (correlated demo data; no gated setup in sample)\n")
    print(state.describe())


if __name__ == "__main__":
    main()
