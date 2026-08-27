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
from types import SimpleNamespace
from typing import Optional

from datetime import timezone

from ict_live.engine import pipeline as v1        # frozen v1 engine, read-only
from ict_live.market import sessions as v1_sessions   # ET/DST-safe session+killzone membership (read-only)
from ict_v2 import align
from ict_v2 import entry_models as EM              # pluggable execution/entry models (FVG + course set)


# ---- sessions / killzones (METHODOLOGY §11, lesson 5) — CONTEXT, never a gate (§1 HTF-is-context) ---
def session_of(dt):
    """(session, killzone) of a timestamp, ET/DST-correct. session ∈ {asia, london_active, ny_am,
    ny_pm, ""}; killzone is the active TRADING killzone (london_active/ny_am/ny_pm) or "". Naive
    datetimes are assumed UTC (mirrors live `_et_iso`) so the ET conversion is correct. Reuses v1's
    frozen `market.sessions`; the course tracks these windows for SIGNIFICANCE/timing, not as a veto."""
    if dt is None:
        return "", ""
    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    windows = v1_sessions.active_windows(dt)                 # windows don't overlap → 0 or 1
    return (windows[0] if windows else ""), (v1_sessions.killzone(dt) or "")


# ---- HTF context labels (METHODOLOGY §17, lesson 8/15) — LABELS, not vetoes -----------------------
# The course labels each setup by its relationship to the higher-timeframe context. The full set is
# the ALIGNMENT axis (aligned vs counter) crossed with the AMD-PHASE read (manipulation vs
# distribution). Only the alignment axis is computable from bias alone; the manipulation/distribution
# refinement needs the AMD phase model (§10) and is added there — NOT invented here.
CONTEXT_LABELS = ("htf-aligned", "counter-context", "possible-manipulation",
                  "possible-distribution", "neutral-context")

# Course fib ladder — the main S/R levels (Lesson 8, §6). 0.5 = equilibrium; 0.62/0.79 = OTE.
FIB_LEVELS = (0.0, 0.5, 0.62, 0.79, 1.0)


def context_label(direction: str, bias: str) -> str:
    """The setup's HTF context label (§17). A LABEL/confidence read, never a veto (§1, §17;
    `config.HTF_IS_VETO=False`). Alignment axis only for now:
      direction == bias → 'htf-aligned'; opposite → 'counter-context'; no/neutral bias → 'neutral-context'.
    The 'possible-manipulation' / 'possible-distribution' AMD-phase refinement arrives with §10."""
    if not bias or bias == "neutral":
        return "neutral-context"
    return "htf-aligned" if direction == bias else "counter-context"


# ---- the three layers -------------------------------------------------------------------------
@dataclass
class HTFContext:
    """Stage 1 — the higher-timeframe context: bias, dealing range, and the liquidity draw."""
    tf: str
    bias: str                              # "long" | "short" | "neutral" (after any anchor veto)
    dealing_range: object = None           # v1 DealingRange (premium/discount/EQ) or None
    liquidity: list = field(default_factory=list)   # active ERL pools = the draw on liquidity (the FULL set)
    anchor_bias: str = ""                  # Daily/Weekly anchor bias ("" if no anchor); vetoes the 4H bias
    anchor_tf: str = ""                    # the anchor timeframe ("D"/"W"), "" if none
    ranges: list = field(default_factory=list)   # ALL dealing ranges on this TF (source_tf-tagged), for nesting
    trend: str = "none"                    # §2/§21 structural trend (up/down/none) — HH/HL rule (Lesson 15)
    trend_change: str = ""                 # §2/§21 trend-change: confirmed | potential | "" (from MSS)

    def zone(self, price: float) -> Optional[str]:
        """premium / discount / equilibrium of `price` within the HTF dealing range (None if no range)."""
        return self.dealing_range.zone_of(price) if self.dealing_range is not None else None

    def erl_irl(self, price: float) -> Optional[str]:
        """Classify a price as EXTERNAL (ERL) or INTERNAL (IRL) range liquidity vs the active dealing
        range (Lesson 10, METHODOLOGY §4): ABOVE the range high or BELOW the range low = ERL (external —
        untaken highs/lows the market draws to); BETWEEN low and high = IRL (internal — FVG/gaps/
        imbalance, a rebalance area). None if no range/price. Recomputed whenever the range updates."""
        dr = self.dealing_range
        if dr is None or price is None:
            return None
        return "ERL" if (price > dr.high or price < dr.low) else "IRL"

    def fib_levels(self) -> list:
        """The course fib ladder (Lesson 8, METHODOLOGY §6): the main support/resistance levels
        0 / 0.5 / 0.62 / 0.79 / 1 measured on the dealing range. ORIENTATION per the course: an
        UPtrend places 0 at the HIGH and 1 at the LOW; a DOWNtrend places 0 at the LOW and 1 at the
        HIGH. Premium/discount of each level is by price vs equilibrium (Lesson 9, direction-agnostic:
        ≥50% premium, <50% discount). Returns [] if no range. 0.5 is equilibrium; 0.62/0.79 = the OTE."""
        dr = self.dealing_range
        if dr is None:
            return []
        span = dr.high - dr.low
        up = dr.direction == "up"                        # uptrend: 0 at high, 1 at low (Lesson 8)
        out = []
        for f in FIB_LEVELS:
            price = (dr.high - f * span) if up else (dr.low + f * span)
            zone = ("equilibrium" if abs(f - 0.5) < 1e-9
                    else "premium" if price > dr.ce else "discount")
            out.append({"level": f, "price": round(price, 2), "zone": zone})
        return out


@dataclass
class GatedSetup:
    """An MTF setup that PASSED the HTF gate, plus the HTF liquidity objective it targets."""
    setup: object                                      # v1 Setup (direction/entry/stop/target)
    objective: object = None                           # HTF liquidity pool = the draw (target)


# progressive ICT workflow stages a candidate can reach (least → most complete)
CANDIDATE_STATES = ("swept", "displaced", "mss", "entry", "actionable")


def _mk(name, status, note="", permanent=False):
    """One pipeline check: status ∈ {ok, fail, pending}; `permanent` marks a REJECTED (vs INCOMPLETE)
    failure so the UI can colour it differently."""
    return {"name": name, "status": status, "note": note, "permanent": permanent}


def _short_reject(txt: str) -> str:
    t = (txt or "").lower()
    if "mitigat" in t:
        return "mitigated"
    if "min_rr" in t or t.startswith("rr "):
        return "RR too low"
    if "degenerate" in t:
        return "stop too tight"
    if "opposing" in t or "liquidity target" in t:
        return "no target"
    if "geometry" in t:
        return "bad geometry"
    return (txt or "invalid")[:24]


def _v1_reject_kind(reject_reason: str) -> str:
    """Classify a v1 rejection. 'rr' = the (course-inspired, [RES]-labelled) 3R minimum, which ICT
    teaches as a QUALITY guideline, not a validity rule — v2 does NOT treat it as a veto. Anything
    else (mitigated FVG / degenerate stop / bad geometry / no liquidity target) is a genuine
    structural/execution invalidation v2 keeps. NB v1 checks RR last, so a pure 'rr' reject means the
    setup already passed every structural check."""
    t = (reject_reason or "").lower()
    return "rr" if ("min_rr" in t or t.startswith("rr ")) else "structural"


def rr_quality(rr):
    """RR as a QUALITY grade, not a validity gate — v2 separates 'valid ICT setup' from 'good trade'.
    reject (≤1, reward ≤ risk) · low (1–2) · good (2–3) · high (≥3), per the user's RR guidance."""
    if rr is None:
        return None
    if rr <= 1.0:
        return "reject"
    if rr < 2.0:
        return "low"
    if rr < 3.0:
        return "good"
    return "high"


def _short_gate(reasons: list) -> str:
    r = (reasons[0] if reasons else "").lower()
    if "bias mismatch" in r:
        return "bias mismatch"
    if "not aligned" in r or "neutral" in r:
        return "bias neutral"
    if "premium/discount" in r or "premium" in r or "discount" in r:
        return "wrong P/D zone"
    if "liquidity objective" in r:
        return "no HTF objective"
    if "geometry" in r:
        return "bad geometry"
    return (reasons[0] if reasons else "")[:24]


def structural_checks(sw, disp, mss, entry, actionable, entry_note=""):
    """The ICT chain as ordered checks — sweep → displacement → MSS → <entry-object> → setup — marking
    where it stopped. MODEL-AGNOSTIC: the entry-object node is labelled from `entry.model` (data, not
    a hardcoded 'FVG'), and its validity is read from the COMMON state only. Returns (checks, complete,
    status): status is None when complete + actionable, else 'incomplete' or 'rejected'. `entry_note`
    labels the setup-node failure."""
    olabel = (entry.model.replace("_", " ") if entry is not None else "entry")   # from the model itself
    checks = [_mk("sweep", "ok")]                                         # the manipulation = the anchor
    if disp is None:
        return checks + [_mk("displacement", "fail", "waiting"), _mk("MSS", "pending"),
                         _mk("entry", "pending"), _mk("setup", "pending")], False, "incomplete"
    checks.append(_mk("displacement", "ok"))
    if mss is None:
        return checks + [_mk("MSS", "fail", "waiting"), _mk("entry", "pending"),
                         _mk("setup", "pending")], False, "incomplete"
    checks.append(_mk("MSS", "ok"))
    if entry is None:
        return checks + [_mk("entry", "fail", "waiting"), _mk("setup", "pending")], False, "incomplete"
    if entry.state in ("completed", "rejected"):                          # common state only
        return checks + [_mk(olabel, "fail", entry.lifecycle or entry.state, True),
                         _mk("setup", "pending")], False, "rejected"
    checks.append(_mk(olabel, "ok"))
    if not actionable:
        return checks + [_mk("setup", "fail", entry_note or "invalid", True)], False, "rejected"
    checks.append(_mk("setup", "ok"))
    return checks, True, None


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
    dealing_range: object = None
    pd_location: Optional[str] = None      # premium | discount | equilibrium of the entry (HTF)
    objective: object = None               # opposing liquidity pool = the draw (target)
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    rr: Optional[float] = None
    rr_quality: Optional[str] = None       # reject | low | good | high — QUALITY grade, not a gate
    actionable: bool = False               # a VALID ICT setup in v2's sense (structure ok, RR>1)
    passed: bool = False                   # cleared the HTF gate (fully validated on this TF)
    entry_model: str = "fvg"               # execution model that produced the entry (fvg | order_block | ...)
    entry_obj: object = None               # the generic Entry (common contract) — None until an entry forms
    status: str = "incomplete"             # passed | incomplete (still developing) | rejected (permanently invalid)
    reasons: list = field(default_factory=list)   # EXPLICIT reasons it did not fully validate (empty ⇒ passed)
    checks: list = field(default_factory=list)    # the ordered pipeline (sweep→…→gate) w/ per-step status
    setup: object = None                   # the assembled v1 Setup, if it reached one
    session: str = ""                      # session window of the manipulation (§11 context; "" if none)
    killzone: str = ""                     # trading killzone of the manipulation (london_active/ny_am/ny_pm)
    context_label: str = "neutral-context" # §17 HTF context label (aligned/counter/neutral) — a label, not a veto

    def to_dict(self) -> dict:
        def px(x):
            return None if x is None else round(float(x), 2)
        obj = self.objective
        return {
            "direction": self.direction, "state": self.state, "status": self.status,
            "entry": px(self.entry), "stop": px(self.stop), "target": px(self.target),
            "rr": (None if self.rr is None else round(float(self.rr), 2)),
            "rr_quality": self.rr_quality,
            "pd_location": self.pd_location,
            "objective": None if obj is None else {"kind": getattr(obj, "kind", None),
                                                   "price": px(getattr(obj, "price", None))},
            "components": {"sweep": self.sweep is not None, "displacement": self.displacement is not None,
                           "mss": self.mss is not None, "entry": self.entry_obj is not None},
            "sweep": None if self.sweep is None else {"pool": px(getattr(self.sweep, "pool_price", None)),
                                                      "extreme": px(getattr(self.sweep, "extreme", None))},
            "mss_state": None if self.mss is None else getattr(self.mss, "state", None),
            "session": self.session, "killzone": self.killzone,   # §11 context (lesson 5)
            "context_label": self.context_label,                   # §17 HTF label (not a veto)
            "actionable": self.actionable, "passed": self.passed, "reasons": list(self.reasons),
            "entry_model": self.entry_model,
            "entry_obj": (self.entry_obj.to_dict() if self.entry_obj is not None else None),  # common contract
            "checks": [dict(c) for c in self.checks],
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
    dealing_range: object = None                       # THIS stage's own dealing range (source_tf-tagged; nesting)


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
    """Stage 4 — the 1m execution trigger. Its own candidates (each confirming the 15m confirmation)
    carry the same explicit reasons/pipeline as the higher stages; the trigger fires only for a gated
    one."""
    tf: str
    fvgs: list = field(default_factory=list)           # ranked LTF entry FVGs
    executables: list = field(default_factory=list)    # list[Executable] (only for gated triggers)
    decision: str = "NO-TRADE"
    cand_info: list = field(default_factory=list)      # ALL 1m candidates as dicts (status/reasons/checks)
    candidate_objs: list = field(default_factory=list)


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
def _bias_from_range(dr) -> str:
    return "long" if (dr and dr.direction == "up") else "short" if (dr and dr.direction == "down") else "neutral"


def trend_state(ms) -> dict:
    """The course trend-change read (Lesson 15, §2/§21) as a VERDICT over v1's existing structural
    skeleton + MSS — no new structural detection, just the interpretation v1 deliberately withholds.

    trend  (Lesson 15: uptrend = higher highs AND higher lows; downtrend = lower highs AND lower lows):
      'up' if the last two structural highs are rising AND the last two lows are rising; 'down' if both
      falling; else 'none' (transition). change:
      'confirmed' if a CONFIRMED MSS exists on this TF (structure actually broke = confirmed trend
      change); 'potential' if an MSS is still potential/candidate (a high/low failed to extend); else ''.
    The same model on the HTF is the main trend; on 1m/5m/15m it is the *intraday* trend change (§21)."""
    swings = getattr(ms, "structural", None) or []
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    rising = lambda seq: len(seq) >= 2 and seq[-1].price > seq[-2].price
    falling = lambda seq: len(seq) >= 2 and seq[-1].price < seq[-2].price
    trend = ("up" if rising(highs) and rising(lows)
             else "down" if falling(highs) and falling(lows) else "none")
    states = {getattr(r.item, "state", "") for r in getattr(ms, "ranked_mss", [])}
    change = ("confirmed" if "confirmed" in states
              else "potential" if (states & {"potential", "candidate"}) else "")
    return {"trend": trend, "change": change}


def htf_bias_of(bars, tf: str) -> str:
    """Directional bias from a timeframe's dealing-range leg — used for the Daily/Weekly ANCHOR."""
    ms = v1.analyze(bars, tf)
    return _bias_from_range(ms.ranges[0] if ms.ranges else None)


def htf_context(bars, tf: str, *, anchor: str = "", anchor_tf: str = "") -> HTFContext:
    """Stage 1: run the engine on the HTF and keep the context layer (bias + dealing range + draw).
    Bias = the direction of the last completed structural leg (up→long, down→short, else neutral).

    OPTIONAL DAILY/WEEKLY ANCHOR: if `anchor` (a higher-TF bias 'long'/'short' from Daily or Weekly)
    is given, the 4H bias must AGREE with it — a 4H bias that OPPOSES the anchor is downgraded to
    NEUTRAL ("trade only with the higher timeframe"). A neutral/empty anchor leaves the 4H bias as-is
    (it never forces a direction the 4H doesn't have). Default off → standard behaviour unchanged."""
    ms = v1.analyze(bars, tf)
    dr = ms.ranges[0] if ms.ranges else None
    bias = _bias_from_range(dr)
    if anchor in ("long", "short") and bias in ("long", "short") and bias != anchor:
        bias = "neutral"                                 # 4H opposes the Daily/Weekly anchor → stand aside
    ts = trend_state(ms)                                 # §2/§21 trend + potential/confirmed change (Lesson 15)
    return HTFContext(tf=tf, bias=bias, dealing_range=dr, liquidity=list(ms.active_erl),
                      anchor_bias=(anchor or ""), anchor_tf=(anchor_tf or ""), ranges=list(ms.ranges),
                      trend=ts["trend"], trend_change=ts["change"])


def generate_candidates(ms, context: HTFContext, entry_models=None, min_stop=None, bars=None) -> list:
    """GENERATE trade candidates from the manipulation, do NOT "find FVGs and filter".

    Every liquidity sweep is a possible trade idea (its direction is set by which side was raided).
    Starting from each sweep we gather whatever of the ICT chain has formed — the displacement off the
    manipulation, the market-structure shift, and the entry FVG — plus the HTF dealing range, the
    premium/discount location of the entry, and the opposing liquidity objective. Where v1 assembled a
    full tradeable Setup we adopt its authoritative geometry/actionability; otherwise the candidate
    still exists at whatever `state` it reached, for the next timeframe to reject / refine / promote.

    DATA-DRIVEN EXECUTION MODELS: the engine never names a model. For each manipulation→displacement,
    it asks every enabled model's detector for entries (`EM.detect`), assembles geometry uniformly
    (`EM.assemble`), validates (universal geometry reject + the model's optional `EM.validate`), gates
    by the HTF context, and builds a Candidate tagged with `entry.model`. Adding a model (order_block,
    breaker, …) is a registry entry, NOT an engine change. See ict_v2/entry_models.py."""
    models = EM.resolve(entry_models)                                    # implemented subset (≥ fvg)
    disp_by_sweep, mss_by_disp = {}, {}
    for r in ms.ranked_displacements:
        d = r.item
        if d.depends_on:
            disp_by_sweep.setdefault(d.depends_on[0], []).append(d)      # depends_on[0] = sweep id
    for r in ms.ranked_mss:
        m = r.item
        if m.depends_on:
            mss_by_disp.setdefault(m.depends_on[0], []).append(m)        # depends_on[0] = displacement id
    active_erl = getattr(ms, "active_erl", [])

    def _partial(sw, disp, mss, direction, dr, objective):
        """Chain has NOT reached an entry object yet (swept / displaced / mss) — model-agnostic."""
        state = "swept" if disp is None else ("displaced" if mss is None else "mss")
        checks, _c, _s = structural_checks(sw, disp, mss, None, False, "")
        checks.append(_mk("HTF context", "pending"))
        reason = {"swept": "Incomplete — no displacement off the manipulation yet",
                  "displaced": "Incomplete — no market-structure shift (MSS) yet",
                  "mss": "Incomplete — structure shifted, no entry yet"}[state]
        sess, kz = session_of(getattr(sw, "time", None))
        return Candidate(direction=direction, state=state, status="incomplete", checks=checks,
                         sweep=sw, displacement=disp, mss=mss, entry_model="", entry_obj=None,
                         dealing_range=dr, pd_location=None, objective=objective,
                         entry=None, stop=getattr(sw, "extreme", None),
                         target=getattr(objective, "price", None), rr=None, rr_quality=None,
                         actionable=False, passed=False, reasons=[reason], setup=None,
                         session=sess, killzone=kz,
                         context_label=context_label(direction, context.bias if context else ""))

    cands = []
    for r in ms.ranked_sweeps:                                           # anchor: the manipulation
        sw = r.item
        direction = "long" if sw.direction == "bullish" else "short"     # sell-side raid → long, buy-side → short
        disps = disp_by_sweep.get(sw.id, [])
        disp = disps[0] if disps else None                               # best-ranked displacement off it
        mss = (mss_by_disp.get(disp.id, []) or [None])[0] if disp is not None else None
        dr = context.dealing_range if context else None
        objective = align.liquidity_objective(context, direction) if context else None

        entries = []                                                     # ask every enabled model
        if disp is not None:
            for name in models:                                          # `bars` handed to EVERY model
                entries += EM.detect(name, disp, mss, ms, direction, bars)
        if not entries:
            cands.append(_partial(sw, disp, mss, direction, dr, objective))
            continue

        for entry in entries:                                            # one candidate per entry object
            geom = EM.assemble(entry, sw.extreme, active_erl, min_stop)  # uniform geometry
            rr = geom["rr"]; quality = rr_quality(rr)
            E, S = geom["entry"], geom["stop"]
            obj = objective                                              # displayed draw = HTF objective
            tgt = getattr(objective, "price", None)
            if tgt is None:                                              # fall back to the setup-TF draw
                tgt = geom["target"]
            actionable = passed = False
            reasons, entry_note = [], ""
            mvok, mvreason = EM.validate(entry.model, entry, geom, context)   # model-specific validation
            if geom["reject"]:                                           # universal geometry reject
                entry_note = _short_reject(geom["reject"]); reasons = [f"Setup not valid — {geom['reject']}"]
            elif not mvok:
                entry_note = (mvreason or "invalid")[:24]; reasons = [f"Setup not valid — {mvreason or 'invalid'}"]
            elif rr is None or rr <= 1.0:                                # RR is a quality metric; only ≤1 rejects
                r_txt = ("%g" % rr) if rr is not None else "?"
                entry_note = f"RR {r_txt} ≤ 1"; reasons = [f"Reward does not exceed risk — RR {r_txt} ≤ 1"]
            else:
                actionable = True
            if actionable:
                ns = SimpleNamespace(direction=direction, entry=E, stop=S)
                passed, gate_reasons, obj2 = align.gate_setup(ns, context)
                if obj2 is not None:
                    obj, tgt = obj2, getattr(obj2, "price", tgt)
                reasons = list(gate_reasons)                             # HTF-gate failures (empty ⇒ passed)

            entry.quality = quality
            entry.reason = reasons[0] if reasons else ""

            checks, complete, sstatus = structural_checks(sw, disp, mss, entry, actionable, entry_note)
            if not complete:
                status = sstatus; checks.append(_mk("HTF context", "pending"))
            elif passed:
                status = "passed"; checks.append(_mk("HTF context", "ok"))
            else:
                status = "rejected"; checks.append(_mk("HTF context", "fail", _short_gate(reasons), True))
            state = "actionable" if actionable else "entry"

            setup_ns = SimpleNamespace(id=entry.id, direction=direction, entry=E, stop=S, target=tgt,
                                       rr=rr, actionable=actionable, reject_reason=geom["reject"],
                                       depends_on=(entry.id,))
            sess, kz = session_of(getattr(sw, "time", None))
            cands.append(Candidate(direction=direction, state=state, status=status, checks=checks,
                                   sweep=sw, displacement=disp, mss=mss,
                                   entry_model=entry.model, entry_obj=entry,
                                   dealing_range=dr, pd_location=(context.zone(E) if context else None),
                                   objective=obj, entry=E, stop=S, target=tgt, rr=rr, rr_quality=quality,
                                   actionable=actionable, passed=passed, reasons=reasons, setup=setup_ns,
                                   session=sess, killzone=kz,
                                   context_label=context_label(direction, context.bias if context else "")))
    return cands


def mtf_setup(bars, tf: str, context: HTFContext, *, refine_bars=None, min_stop=None,
              entry_models=None) -> MTFSetup:
    """Stage 2: GENERATE trade candidates on this timeframe (manipulation → full ICT idea), then GATE
    the tradeable ones by the HTF context (bias + premium/discount + liquidity objective). Every
    candidate is retained (with its workflow `state`) so the next timeframe can reject / refine /
    promote it; cand_info carries all of them so the UI shows all-possible (grey) / available (white,
    reached `actionable`) / passed-gate (bold).

    OPTIONAL MTF ENTRY REFINEMENT: if `refine_bars` (a LOWER-TF bar window covering the same span,
    already truncated at the cursor) is given, the entry FVG is ALSO sought on that lower TF inside
    each displacement (v1's built-in mechanism; the HTF sweep/MSS are never redefined) — this gives a
    fast instrument a fresh, unmitigated entry gap. `min_stop` rejects degenerate stops. Both default
    off, so the standard v2 behaviour is unchanged."""
    ms = v1.analyze(bars, tf, refine_bars=refine_bars, min_stop=min_stop)
    all_cands = generate_candidates(ms, context, entry_models=entry_models, min_stop=min_stop, bars=bars)
    gated, candidates, cand_info = [], [], []
    for c in all_cands:
        cand_info.append(c.to_dict())
        if c.actionable and c.setup is not None:
            candidates.append(c.setup)
            if c.passed:
                gated.append(GatedSetup(setup=c.setup, objective=c.objective))
    return MTFSetup(tf=tf, sweeps=list(ms.ranked_sweeps), displacements=list(ms.ranked_displacements),
                    mss=list(ms.ranked_mss), candidates=candidates, gated=gated, cand_info=cand_info,
                    candidate_objs=all_cands, dealing_range=(ms.ranges[0] if ms.ranges else None))


def confirm_setup(bars, tf: str, context: HTFContext, setup: MTFSetup, *,
                  against: str = "1H setup", node: str = "confirms 1H",
                  refine_bars=None, min_stop=None, entry_models=None) -> MTFSetup:
    """A CONFIRMATION stage. Generate candidates on `tf` with their OWN structure, HTF-gate them like
    the 1H stage, THEN require they confirm the higher layer (`setup`): a confirmation is valid only if
    it is a complete, HTF-aligned setup in the direction of a gated setup on that higher layer. Each
    candidate keeps EXPLICIT reasons AND its step-by-step pipeline, with a final `node` check
    ("confirms 1H" for 15m, "1m trigger" for 1m) that fails as INCOMPLETE ("No gated ... to confirm")
    or REJECTED ("Direction mismatch"). `refine_bars` optionally refines THIS stage's entry FVG onto a
    lower TF (e.g. the 15m confirmation onto 5m); `min_stop` rejects degenerate stops."""
    mtf = mtf_setup(bars, tf, context, refine_bars=refine_bars, min_stop=min_stop,
                    entry_models=entry_models)                  # generate + HTF-gate
    setup_dirs = sorted({g.setup.direction for g in (setup.gated if setup else [])})
    gated, candidates, cand_info = [], [], []
    for c in mtf.candidate_objs:                             # the rich Candidate objects from the generate
        if c.actionable and c.passed:                        # passed the HTF gate → evaluate confirmation
            if not setup_dirs:
                c.reasons = list(c.reasons) + [f"No gated {against} to confirm"]
                c.passed, c.status = False, "incomplete"
                c.checks.append(_mk(node, "fail", "no " + against, False))
            elif c.direction not in setup_dirs:
                c.reasons = list(c.reasons) + [
                    f"Direction mismatch — {tf} {c.direction} vs {against} {'/'.join(setup_dirs)}"]
                c.passed, c.status = False, "rejected"
                c.checks.append(_mk(node, "fail", "wrong direction", True))
            else:
                c.checks.append(_mk(node, "ok"))             # confirmed in the higher-layer direction
        else:                                                # HTF-failed or structurally incomplete
            c.checks.append(_mk(node, "pending"))
        cand_info.append(c.to_dict())
        if c.actionable and c.setup is not None:
            candidates.append(c.setup)
            if c.passed:
                gated.append(GatedSetup(setup=c.setup, objective=c.objective))
    return MTFSetup(tf=tf, sweeps=mtf.sweeps, displacements=mtf.displacements, mss=mtf.mss,
                    candidates=candidates, gated=gated, cand_info=cand_info,
                    candidate_objs=mtf.candidate_objs, dealing_range=mtf.dealing_range)


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


def execution_for(bars, tf: str, context, setup, confirmation, *, min_stop=None,
                  entry_models=None) -> LTFExecution:
    """Stage 4 — the 1m execution trigger. It GENERATES 1m candidates that must confirm the 15m
    confirmation (own structure, HTF-aligned, same direction as a gated 15m confirmation), exactly the
    same candidate+reasons+pipeline model as the higher stages. The trade fires only for a gated 1m
    candidate; the top-line decision still reports how far the cascade got. Like the 1H and 15m stages,
    the 1m candidate list is ALWAYS generated (whenever a directional context exists) so the stage is
    just as transparent — every 1m candidate carries its explicit reasons/checks and the '1m trigger'
    node — instead of staying empty until the cascade happens to be gated. NB the 1m entry FVG is
    already the finest timeframe, so there is no lower TF to refine it onto — `min_stop` (degenerate-
    stop floor) is the only refinement-mode parameter that applies here."""
    if context is None or context.bias == "neutral":
        return LTFExecution(tf=tf, decision="NO-TRADE (no context bias)")
    # top-line decision from the cascade state (independent of the 1m candidate universe)
    if not (setup and setup.gated):
        decision = "NO-TRADE (no 1H setup)"
    elif not (confirmation and confirmation.gated):
        decision = "NO-TRADE (awaiting 15m confirmation)"
    else:
        decision = None                                      # resolved after generating the 1m candidates
    if not bars:                                             # degenerate/unit-test path: no bars to analyse
        return LTFExecution(tf=tf, decision=decision or "NO-TRADE (awaiting 1m trigger)")
    # ALWAYS generate the 1m candidate list (transparency parity with the higher stages)
    conf = confirm_setup(bars, tf, context, confirmation, against="15m confirmation", node="1m trigger",
                         min_stop=min_stop, entry_models=entry_models)
    executables = []
    if decision is None:                                     # cascade reached the 1m: fire iff a gated 1m trigger
        if conf.gated:
            decision = "LONG" if conf.gated[0].setup.direction == "long" else "SHORT"
            executables = [Executable(direction=g.setup.direction, entry=g.setup.entry, stop=g.setup.stop,
                                      target=(getattr(g.objective, "price", None) if g.objective is not None
                                              else g.setup.target), ltf_confirmed=True, objective=g.objective)
                           for g in conf.gated]
        else:
            decision = "NO-TRADE (awaiting 1m trigger)"
    return LTFExecution(tf=tf, fvgs=[], executables=executables, decision=decision,
                        cand_info=conf.cand_info, candidate_objs=conf.candidate_objs)


def analyze_mtf(context_bars, setup_bars, confirm_bars, trigger_bars, *, context_tf: str = "4H",
                setup_tf: str = "1H", confirm_tf: str = "15m", trigger_tf: str = "1m",
                anchor_bars=None, anchor_tf: str = "", entry_models=None) -> MTFState:
    """Run the four-layer cascade in order and return the combined state (stateless convenience).
    Optional Daily/Weekly anchor: pass `anchor_bars` on `anchor_tf` ("D"/"W") to veto a counter-trend
    4H bias to neutral. `entry_models` selects the execution models (default FVG only)."""
    anchor = htf_bias_of(anchor_bars, anchor_tf) if (anchor_bars and anchor_tf) else ""
    ctx = htf_context(context_bars, context_tf, anchor=anchor, anchor_tf=(anchor_tf if anchor else ""))
    stp = mtf_setup(setup_bars, setup_tf, ctx, entry_models=entry_models)
    cf = confirm_setup(confirm_bars, confirm_tf, ctx, stp, entry_models=entry_models)  # 15m confirms 1H
    exe = execution_for(trigger_bars, trigger_tf, ctx, stp, cf, entry_models=entry_models)
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
