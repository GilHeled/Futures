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


# progressive ICT workflow stages a candidate can reach (least → most complete)
CANDIDATE_STATES = ("swept", "displaced", "mss", "fvg", "actionable")


@dataclass
class Candidate:
    """A COMPLETE trade idea for one timeframe — everything the engine knows about a possible setup,
    anchored on the manipulation (liquidity sweep). It is NOT "an FVG we filtered": it is generated
    from the manipulation and then carries whatever of the ICT chain has formed so far
    (displacement → MSS → FVG), plus the HTF dealing range, premium/discount location, and liquidity
    objective. `state` says how far through the workflow it got; the next timeframe rejects / refines
    / promotes it."""
    direction: str                         # long | short
    state: str                             # one of CANDIDATE_STATES
    sweep: object = None                   # the manipulation (the anchor — always present)
    displacement: object = None
    mss: object = None
    fvg: object = None
    dealing_range: object = None
    pd_location: Optional[str] = None      # premium | discount | equilibrium of the entry (HTF)
    objective: object = None               # opposing liquidity pool = the draw (target)
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    rr: Optional[float] = None
    actionable: bool = False               # v1 assembled a tradeable setup for it
    passed: bool = False                   # cleared the HTF gate (fully validated on this TF)
    reasons: list = field(default_factory=list)   # EXPLICIT reasons it did not fully validate (empty ⇒ passed)
    setup: object = None                   # the assembled v1 Setup, if it reached one

    def to_dict(self) -> dict:
        def px(x):
            return None if x is None else round(float(x), 2)
        obj = self.objective
        return {
            "direction": self.direction, "state": self.state,
            "entry": px(self.entry), "stop": px(self.stop), "target": px(self.target),
            "rr": (None if self.rr is None else round(float(self.rr), 2)),
            "pd_location": self.pd_location,
            "objective": None if obj is None else {"kind": getattr(obj, "kind", None),
                                                   "price": px(getattr(obj, "price", None))},
            "components": {"sweep": self.sweep is not None, "displacement": self.displacement is not None,
                           "mss": self.mss is not None, "fvg": self.fvg is not None},
            "sweep": None if self.sweep is None else {"pool": px(getattr(self.sweep, "pool_price", None)),
                                                      "extreme": px(getattr(self.sweep, "extreme", None))},
            "fvg_status": None if self.fvg is None else getattr(self.fvg, "status", None),
            "mss_state": None if self.mss is None else getattr(self.mss, "state", None),
            "actionable": self.actionable, "passed": self.passed, "reasons": list(self.reasons),
            "id": (getattr(self.setup, "id", "") if self.setup is not None
                   else getattr(self.sweep, "id", "")),
        }


@dataclass
class MTFSetup:
    """Stage 2 — the intermediate-timeframe setup: manipulation, displacement, MSS, and the setups
    that survive the HTF gate."""
    tf: str
    sweeps: list = field(default_factory=list)         # ranked manipulation (liquidity raids)
    displacements: list = field(default_factory=list)
    mss: list = field(default_factory=list)
    candidates: list = field(default_factory=list)     # ACTIONABLE MTF setups (the available pool)
    gated: list = field(default_factory=list)          # list[GatedSetup] that passed the HTF gate
    cand_info: list = field(default_factory=list)      # ALL candidates as dicts (w/ actionable/passed/reasons)
    candidate_objs: list = field(default_factory=list) # the rich Candidate objects (for the next stage to refine)


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
    """The full four-layer cascade: 4H context -> 1H setup -> 15m confirmation -> 1m execution."""
    context: HTFContext
    setup: MTFSetup                       # 1H primary setup (gated by context)
    confirmation: MTFSetup = None         # 15m confirmation (its own structure, in the setup direction)
    execution: LTFExecution = None        # 1m execution trigger

    def describe(self) -> str:
        c, s, cf, e = self.context, self.setup, self.confirmation, self.execution
        dr = c.dealing_range if c else None
        drs = f"{dr.low:g}-{dr.high:g} (CE {dr.ce:g}, {dr.direction})" if dr else "none"
        ng = len(cf.gated) if cf else 0
        ex = e.executables[0] if (e and e.executables) else None
        exs = (f"{ex.direction} entry {ex.entry:g} stop {ex.stop:g} target "
               f"{('%g'%ex.target) if ex.target is not None else '—'}") if ex else (e.decision if e else "—")
        return (
            f"[1] CONTEXT   ({c.tf if c else '?'}): bias={c.bias if c else '?'}  dealing_range={drs}\n"
            f"[2] SETUP     ({s.tf if s else '?'}): gated={len(s.gated) if s else 0} of {len(s.candidates) if s else 0}\n"
            f"[3] CONFIRM   ({cf.tf if cf else '?'}): gated={ng}\n"
            f"[4] EXECUTION ({e.tf if e else '?'}): {exs}"
        )


# ---- the three stages -------------------------------------------------------------------------
def htf_context(bars, tf: str) -> HTFContext:
    """Stage 1: run the engine on the HTF and keep the context layer (bias + dealing range + draw)."""
    ms = v1.analyze(bars, tf)
    dr = ms.ranges[0] if ms.ranges else None
    bias = "long" if (dr and dr.direction == "up") else "short" if (dr and dr.direction == "down") else "neutral"
    return HTFContext(tf=tf, bias=bias, dealing_range=dr, liquidity=list(ms.active_erl))


def generate_candidates(ms, context: HTFContext) -> list:
    """GENERATE trade candidates from the manipulation, do NOT "find FVGs and filter".

    Every liquidity sweep is a possible trade idea (its direction is set by which side was raided).
    Starting from each sweep we gather whatever of the ICT chain has formed — the displacement off the
    manipulation, the market-structure shift, and the entry FVG — plus the HTF dealing range, the
    premium/discount location of the entry, and the opposing liquidity objective. Where v1 assembled a
    full tradeable Setup we adopt its authoritative geometry/actionability; otherwise the candidate
    still exists at whatever `state` it reached, for the next timeframe to reject / refine / promote."""
    disp_by_sweep, fvg_by_disp, mss_by_disp = {}, {}, {}
    for r in ms.ranked_displacements:
        d = r.item
        if d.depends_on:
            disp_by_sweep.setdefault(d.depends_on[0], []).append(d)      # depends_on[0] = sweep id
    for r in ms.ranked_fvgs:
        f = r.item
        if f.depends_on:
            fvg_by_disp.setdefault(f.depends_on[0], []).append(f)        # depends_on[0] = displacement id
    for r in ms.ranked_mss:
        m = r.item
        if m.depends_on:
            mss_by_disp.setdefault(m.depends_on[0], []).append(m)        # depends_on[0] = displacement id
    setup_by_fvg = {}
    for r in ms.ranked_setups:
        su = r.item
        deps = getattr(su, "depends_on", None) or ()
        if deps:
            setup_by_fvg[deps[0]] = su                                   # depends_on[0] = source FVG id

    cands = []
    for r in ms.ranked_sweeps:                                           # anchor: the manipulation
        sw = r.item
        direction = "long" if sw.direction == "bullish" else "short"     # sell-side raid → long, buy-side → short
        disps = disp_by_sweep.get(sw.id, [])
        disp = disps[0] if disps else None                               # best-ranked displacement off it
        fvg = mss = setup = None
        if disp is not None:
            fvgs = fvg_by_disp.get(disp.id, [])
            fvgs = sorted(fvgs, key=lambda f: 0 if getattr(f, "status", "") != "mitigated" else 1)
            fvg = fvgs[0] if fvgs else None
            msss = mss_by_disp.get(disp.id, [])
            mss = msss[0] if msss else None
        if fvg is not None:
            setup = setup_by_fvg.get(getattr(fvg, "id", None))

        stop = sw.extreme                                                # stop at the manipulation extreme
        entry = getattr(fvg, "ce", None) if fvg is not None else None
        dr = context.dealing_range if context else None
        pd = context.zone(entry) if (context and entry is not None) else None
        objective = align.liquidity_objective(context, direction) if context else None
        target = getattr(objective, "price", None) if objective is not None else None

        actionable = passed = False
        reasons = []
        if setup is not None:                                            # adopt v1's authoritative setup
            entry, stop = setup.entry, setup.stop
            if target is None:
                target = setup.target
            rr = getattr(setup, "rr", None)
            actionable = bool(getattr(setup, "actionable", False))
            if actionable:
                passed, gate_reasons, obj2 = align.gate_setup(setup, context)
                if obj2 is not None:
                    objective, target = obj2, getattr(obj2, "price", target)
                reasons = list(gate_reasons)                             # HTF-gate failures (empty ⇒ passed)
            else:                                                        # v1 rejected the assembled setup
                rr_txt = getattr(setup, "reject_reason", "") or "not a valid setup"
                reasons = [f"Setup not valid — {rr_txt}"]
        else:
            risk = abs(stop - entry) if entry is not None else 0.0
            reward = abs(entry - target) if (entry is not None and target is not None) else 0.0
            rr = round(reward / risk, 2) if risk > 0 else None

        if disp is None:
            state = "swept"
        elif actionable:
            state = "actionable"
        elif fvg is not None:
            state = "fvg"
        elif mss is not None:
            state = "mss"
        else:
            state = "displaced"
        if not reasons and not passed:                                   # partial idea: explain the stage reached
            reasons = [{"swept": "Incomplete — no displacement off the manipulation yet",
                        "displaced": "Incomplete — no market-structure shift (MSS) yet",
                        "mss": "Incomplete — structure shifted, no valid entry FVG yet",
                        "fvg": "Incomplete — entry FVG present, not yet a valid setup"}.get(state, "Incomplete")]

        cands.append(Candidate(direction=direction, state=state, sweep=sw, displacement=disp, mss=mss,
                               fvg=fvg, dealing_range=dr, pd_location=pd, objective=objective,
                               entry=entry, stop=stop, target=target, rr=rr, actionable=actionable,
                               passed=passed, reasons=reasons, setup=setup))
    return cands


def mtf_setup(bars, tf: str, context: HTFContext) -> MTFSetup:
    """Stage 2: GENERATE trade candidates on this timeframe (manipulation → full ICT idea), then GATE
    the tradeable ones by the HTF context (bias + premium/discount + liquidity objective). Every
    candidate is retained (with its workflow `state`) so the next timeframe can reject / refine /
    promote it; cand_info carries all of them so the UI shows all-possible (grey) / available (white,
    reached `actionable`) / passed-gate (bold)."""
    ms = v1.analyze(bars, tf)
    all_cands = generate_candidates(ms, context)
    gated, candidates, cand_info = [], [], []
    for c in all_cands:
        cand_info.append(c.to_dict())
        if c.actionable and c.setup is not None:
            candidates.append(c.setup)
            if c.passed:
                gated.append(GatedSetup(setup=c.setup, objective=c.objective))
    return MTFSetup(tf=tf, sweeps=list(ms.ranked_sweeps), displacements=list(ms.ranked_displacements),
                    mss=list(ms.ranked_mss), candidates=candidates, gated=gated, cand_info=cand_info,
                    candidate_objs=all_cands)


def confirm_setup(bars, tf: str, context: HTFContext, setup: MTFSetup) -> MTFSetup:
    """Stage 3 — the 15m CONFIRMATION. Generate 15m candidates with their OWN structure
    (sweep/displacement/MSS/FVG), gate them by the HTF context exactly like the 1H stage, THEN require
    they confirm the 1H setup: a confirmation is valid only if it is a complete 15m setup, HTF-aligned,
    AND in the direction of a gated 1H setup. Every candidate keeps EXPLICIT reasons — the HTF-gate
    reasons plus the confirmation reason — so this stage explains each rejection just like the 1H stage
    ("No gated 1H setup to confirm" / "Direction mismatch — 15m X vs 1H setup Y")."""
    mtf = mtf_setup(bars, tf, context)                       # generate + HTF-gate (reasons already attached)
    setup_dirs = sorted({g.setup.direction for g in (setup.gated if setup else [])})
    gated, candidates, cand_info = [], [], []
    for c in mtf.candidate_objs:                             # the rich Candidate objects from the 15m generate
        if c.actionable:                                     # only a COMPLETE 15m setup can confirm the 1H
            if not setup_dirs:
                c.reasons = list(c.reasons) + ["No gated 1H setup to confirm"]
                c.passed = False
            elif c.direction not in setup_dirs:
                c.reasons = list(c.reasons) + [
                    f"Direction mismatch — 15m {c.direction} vs 1H setup {'/'.join(setup_dirs)}"]
                c.passed = False
            # else: keep c.passed from the HTF gate — a confirmed 15m setup in the 1H direction
        cand_info.append(c.to_dict())
        if c.actionable and c.setup is not None:
            candidates.append(c.setup)
            if c.passed:
                gated.append(GatedSetup(setup=c.setup, objective=c.objective))
    return MTFSetup(tf=tf, sweeps=mtf.sweeps, displacements=mtf.displacements, mss=mtf.mss,
                    candidates=candidates, gated=gated, cand_info=cand_info)


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


def execution_for(bars, tf: str, context, setup, confirmation) -> LTFExecution:
    """The 1m execution fires ONLY when the whole cascade holds: a directional context, a gated 1H
    setup, AND a gated 15m confirmation. Otherwise it returns a NO-TRADE that says how far the cascade
    got (so the dashboard can show the stage reached). The executable is built from the 15m
    confirmation (the finer, confirmed structure), targeting the HTF liquidity draw, and 1m-confirmed
    by an entry FVG."""
    if context is None or context.bias == "neutral":
        return LTFExecution(tf=tf, decision="NO-TRADE (no context bias)")
    if not (setup and setup.gated):
        return LTFExecution(tf=tf, decision="NO-TRADE (no 1H setup)")
    if not (confirmation and confirmation.gated):
        return LTFExecution(tf=tf, decision="NO-TRADE (awaiting 15m confirmation)")
    exe = ltf_execution(bars, tf, confirmation, context)
    if not exe.executables:
        return LTFExecution(tf=tf, fvgs=exe.fvgs, executables=[], decision="NO-TRADE (awaiting 1m trigger)")
    return exe


def analyze_mtf(context_bars, setup_bars, confirm_bars, trigger_bars, *, context_tf: str = "4H",
                setup_tf: str = "1H", confirm_tf: str = "15m", trigger_tf: str = "1m") -> MTFState:
    """Run the four-layer cascade in order and return the combined state (stateless convenience)."""
    ctx = htf_context(context_bars, context_tf)
    stp = mtf_setup(setup_bars, setup_tf, ctx)
    cf = confirm_setup(confirm_bars, confirm_tf, ctx, stp)  # 15m confirmation of the 1H setup
    exe = execution_for(trigger_bars, trigger_tf, ctx, stp, cf)
    return MTFState(context=ctx, setup=stp, confirmation=cf, execution=exe)


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
    """Build 4H/1H/15m/1m from one 1m base (so they're consistent) and run the four-layer cascade."""
    base = _base_1m(20000, seed)                 # ~13 sessions of 1m
    return analyze_mtf(resample(base, 240, "4H"), resample(base, 60, "1H"), resample(base, 15, "15m"),
                       base[-400:], context_tf="4H", setup_tf="1H", confirm_tf="15m", trigger_tf="1m")


def main() -> None:
    # search a few seeds for one that actually forms a 1H setup, to show the cascade firing
    state = None
    for seed in (7, 11, 23, 42, 101, 202, 303):
        st = demo_state(seed)
        if st.setup.gated:
            state = st
            print(f"ICT v2 — 4H context -> 1H setup -> 15m confirm -> 1m execution (seed={seed})\n")
            break
    if state is None:
        state = demo_state(7)
        print("ICT v2 — cascade (correlated demo data; no 1H setup in sample)\n")
    print(state.describe())


if __name__ == "__main__":
    main()
