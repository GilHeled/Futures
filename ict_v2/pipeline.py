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

import re
from datetime import timezone

from ict_live.engine import pipeline as v1        # frozen v1 engine, read-only
from ict_live.market import sessions as v1_sessions   # ET/DST-safe session+killzone membership (read-only)
from ict_v2 import align
from ict_v2 import entry_models as EM              # pluggable execution/entry models (FVG + course set)
from ict_v2 import pdarrays as PD                 # role-neutral PD-array objects + contextual role (Lessons 10-12)
from ict_v2 import recommend as REC               # the semantic layer: structure / quality / filters / recommendation


# ---- ≥15-minute liquidity floor (Lesson 6 & 8): liquidity/swings are NOT marked below 15m ----------
_MIN_LIQUIDITY_TF_MIN = 15


def tf_minutes(tf: str) -> int:
    """Timeframe string → minutes ('4H'→240, '1H'→60, '15m'→15, '1m'→1, 'D'→1440, 'W'→10080). 0 if
    unparseable (treated as 'unknown', not a violation)."""
    m = re.match(r"^\s*(\d*)\s*([mMhHdDwW])\s*$", tf or "")
    if not m:
        return 0
    return int(m.group(1) or 1) * {"m": 1, "h": 60, "d": 1440, "w": 10080}[m.group(2).lower()]


def assert_liquidity_floor(*tfs) -> None:
    """Enforce the course's ≥15-minute liquidity floor (Lesson 6 & 8): the STRUCTURAL / liquidity
    timeframes (context / setup / confirmation) must be ≥15m — 'we do not mark liquidity below the
    15-minute chart'. The execution trigger (and any refine TF) may be finer: they only TRIGGER an
    entry, they do not DEFINE liquidity/swings. Raises ValueError on a violation."""
    for tf in tfs:
        mins = tf_minutes(tf)
        if 0 < mins < _MIN_LIQUIDITY_TF_MIN:
            raise ValueError(f"structure/liquidity timeframe {tf!r} < 15m violates the course "
                             f"≥15-minute liquidity floor (Lesson 6/8)")


def pullback_pct(disp, entry_price):
    """How deep the entry retraces into the displacement leg, as a fraction of the leg (Lesson 8:
    'every pullback retraces at least 50% to continue the trend'). Measured from the leg's END back
    toward its START; ≥0.5 is a course-adequate pullback. None if the leg/entry is unknown."""
    if disp is None or entry_price is None:
        return None
    a, b = getattr(disp, "start_price", None), getattr(disp, "end_price", None)
    if a is None or b is None or a == b:
        return None
    return round(abs(b - entry_price) / abs(b - a), 2)


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

# Power-of-3 / AMD phases (Lesson 16, §10). "accumulation" is NEVER emitted — detecting consolidation
# needs a range/duration threshold the course does not define (config.CONSOLIDATION_DETECTOR sentinel).
AMD_PHASES = ("accumulation", "manipulation", "distribution", "")


def amd_phase(direction: str, bias: str, mss_state: str) -> str:
    """The Power-of-3 / AMD phase of a sweep-anchored candidate (Lesson 16, §10).
    Phases: accumulation (consolidation) → manipulation (the counter-move sweep that takes liquidity)
    → distribution (the real move WITH the main trend). Lesson 16 marks the transition explicitly:
    the intraday TREND CHANGE — a CONFIRMED MSS — is when manipulation gives way to the real move.
    So from a candidate (always anchored on a sweep = the manipulation event):
      'distribution' — a confirmed MSS aligned with the HTF bias (the real move is underway);
      'manipulation' — otherwise (swept, but the aligned trend change is not yet confirmed).
    'accumulation' is intentionally never returned (consolidation detector is parameter-undefined)."""
    if mss_state == "confirmed" and bias in ("long", "short") and direction == bias:
        return "distribution"
    return "manipulation"


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
    draws: list = field(default_factory=list)   # HTF PD-array DRAWS (role='draw' FVGs): objectives price seeks (Lesson 11/16)

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


def _reject_is_structural(reject: str) -> bool:
    """Classify an `assemble()` reject. STRUCTURAL (→ invalid setup): degenerate stop / bad geometry.
    NOT structural (→ a course-filter / quality concern, structure stays valid): a missing liquidity
    TARGET — the setup is a real ICT setup even if there is no draw to target (the ≥3R filter handles
    that). Mitigated/invalidated entries are detected earlier via the entry's common state."""
    t = (reject or "").lower()
    if "target" in t or "opposing" in t:
        return False
    return bool(t)                                   # degenerate stop / geometry / any other = structural


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


def structural_checks(sw, disp, mss, entry, structure, struct_reason=""):
    """The STRUCTURE layer as an ordered chain — sweep → displacement → MSS → <entry-object> — marking
    where it stopped. MODEL-AGNOSTIC: the entry node is labelled from `entry.model` (data, not a
    hardcoded 'FVG'). This is STRUCTURE ONLY: RR, HTF bias, and premium/discount are NOT here (they are
    quality / course-filters). `structure` ∈ {forming, valid, invalid}; `struct_reason` labels an
    invalid entry node. Returns the checks list."""
    olabel = (entry.model.replace("_", " ") if entry is not None else "entry")   # from the model itself
    checks = [_mk("sweep", "ok")]                                         # the manipulation = the anchor
    if disp is None:
        return checks + [_mk("displacement", "fail", "no move yet"), _mk("MSS", "pending"), _mk("entry", "pending")]
    checks.append(_mk("displacement", "ok"))
    if mss is None:
        return checks + [_mk("MSS", "fail", "not shifted"), _mk("entry", "pending")]
    checks.append(_mk("MSS", "ok"))
    if entry is None:
        return checks + [_mk("entry", "fail", "no FVG yet")]
    if structure == "invalid":
        return checks + [_mk(olabel, "fail", _short_reject(struct_reason), True)]
    checks.append(_mk(olabel, "ok"))
    # the entry is present + structurally valid → show the RETRACE/fill step (§14: enter on the retrace)
    if getattr(entry, "state", "") == "valid":            # FVG has been retraced into → live
        return checks + [_mk("retrace", "ok")]
    return checks + [_mk("retrace", "fail", "awaiting", False)]   # armed FVG — waiting for the retrace


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
    amd_phase: str = "manipulation"        # §10 Power-of-3 phase (manipulation/distribution; Lesson 16)
    # --- the four semantic layers (see ict_v2/recommend.py) ---
    pullback: "float|None" = None          # QUALITY: entry retrace depth into the displacement leg (§/Lesson 8; ≥0.5 good)
    fvg_tiebreak: int = 0                  # # of unmitigated FVGs the entry was picked among; >1 ⇒ [RES:fvg_tiebreak] exercised
    entry_role: str = ""                   # CONTEXT ROLE of the entry PD array: entry (LTF) | reaction | draw (Lessons 10-12)
    entry_role_basis: dict = field(default_factory=dict)   # WHY the role (tf_class / zone / side / lifecycle)
    tf: str = ""                           # the timeframe this candidate's structure lives on (§1; chart-mappable)
    structure: str = "forming"             # STRUCTURE: forming | valid | invalid (the ICT setup itself)
    filters: list = field(default_factory=list)   # COURSE FILTERS: [{name, ok, reason}] (≥3R, killzone, …)
    recommendation: str = "WATCH"          # RECOMMENDATION: TAKE | SKIP | WATCH (derived from the layers)

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
                                                   "price": px(getattr(obj, "price", None)),
                                                   "klass": getattr(obj, "klass", "ERL"),        # ERL|IRL (Lesson 10)
                                                   "array_kind": getattr(obj, "array_kind", "swing")},  # swing|fvg|nwog|org
            "components": {"sweep": self.sweep is not None, "displacement": self.displacement is not None,
                           "mss": self.mss is not None, "entry": self.entry_obj is not None},
            "sweep": None if self.sweep is None else {"pool": px(getattr(self.sweep, "pool_price", None)),
                                                      "extreme": px(getattr(self.sweep, "extreme", None))},
            "mss_state": None if self.mss is None else getattr(self.mss, "state", None),
            "leg": (None if self.displacement is None else {   # WHICH displacement leg this candidate tracks
                "from": px(getattr(self.displacement, "start_price", None)),
                "to": px(getattr(self.displacement, "end_price", None)),
                "bars": [getattr(self.displacement, "start_index", None),
                         getattr(self.displacement, "end_index", None)],
                "dir": getattr(self.displacement, "direction", None),
                "id": getattr(self.displacement, "id", None)}),
            "session": self.session, "killzone": self.killzone,   # §11 context (lesson 5)
            "context_label": self.context_label,                   # §17 HTF label (not a veto)
            "amd_phase": self.amd_phase,                           # §10 Power-of-3 phase (Lesson 16)
            "pullback": self.pullback,                             # QUALITY: retrace depth of the leg (≥0.5 good, Lesson 8)
            "fvg_tiebreak": self.fvg_tiebreak,                     # >1 ⇒ [RES:fvg_tiebreak] picked among N unmitigated FVGs
            "entry_role": self.entry_role,                         # PD-array role of the entry (entry/reaction/draw) — Lessons 10-12
            "entry_role_basis": dict(self.entry_role_basis),       # WHY (tf_class/zone/side/lifecycle)
            "tf": self.tf,                                         # the timeframe this candidate's structure lives on (§1)
            "structure": self.structure,                           # STRUCTURE: forming|valid|invalid
            "filters": [dict(f) for f in self.filters],            # COURSE FILTERS: [{name,ok,reason}]
            "recommendation": self.recommendation,                 # RECOMMENDATION: TAKE|SKIP|WATCH
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
    ctx = HTFContext(tf=tf, bias=bias, dealing_range=dr, liquidity=list(ms.active_erl),
                     anchor_bias=(anchor or ""), anchor_tf=(anchor_tf or ""), ranges=list(ms.ranges),
                     trend=ts["trend"], trend_change=ts["change"])
    # HTF PD-array DRAWS (Lesson 11/16): a higher-timeframe FVG on the DRAW side of the dealing range
    # is an objective price is pulled toward. Role-neutral detection (v1's FVGs) → contextual role;
    # keep the ones the context labels 'draw'. Needs a range + a directional bias to have a draw side.
    if dr is not None and bias in ("long", "short"):
        for r in getattr(ms, "ranked_fvgs", []):
            arr = PD.role_of(PD.from_fvg(r.item, tf), direction=bias,
                             zone=dr.zone_of(r.item.ce), erl_irl=ctx.erl_irl(r.item.ce))
            if arr.role == "draw":
                ctx.draws.append(arr)
    return ctx


def generate_candidates(ms, context: HTFContext, entry_models=None, min_stop=None, bars=None,
                        filters_cfg=None, tf: str = "") -> list:
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
    # Role-neutral PD arrays for THIS stage (Lessons 10-12): the unfilled FVGs are the internal-imbalance
    # candidates the ERL/IRL-aware draw selection falls back to when no external pool remains to seek.
    stage_arrays = [PD.from_fvg(r.item, tf) for r in getattr(ms, "ranked_fvgs", [])]
    stage_unfilled = [a for a in stage_arrays if a.status != "mitigated"]

    def _partial(sw, disp, mss, direction, dr, objective):
        """Chain has NOT reached an entry object yet (swept / displaced / mss) — model-agnostic.
        STRUCTURE is 'forming' → RECOMMENDATION WATCH (a developing idea, no course filters yet)."""
        state = "swept" if disp is None else ("displaced" if mss is None else "mss")
        # name the specific level/leg so near-looking candidates are distinguishable on the chart
        swept = f" (swept {getattr(sw, 'pool_price', 0):g}, extreme {getattr(sw, 'extreme', 0):g})"
        leg = ("" if disp is None else
               f" [{disp.direction} {disp.start_price:g}→{disp.end_price:g}, bars {disp.start_index}–{disp.end_index}]")
        reason = {"swept": f"Waiting for a displacement (energetic move) off the manipulation{swept}",
                  "displaced": f"Waiting for a market-structure shift (MSS) on the displacement{leg}",
                  "mss": f"Waiting for an entry FVG on the displacement leg{leg}"}[state]
        checks = structural_checks(sw, disp, mss, None, "forming", "")
        rec, rec_reasons = REC.recommend(structure="forming", structure_reason=reason)
        sess, kz = session_of(getattr(sw, "time", None))
        _bias = context.bias if context else ""
        return Candidate(direction=direction, state=state, status="incomplete", checks=checks,
                         sweep=sw, displacement=disp, mss=mss, entry_model="", entry_obj=None,
                         dealing_range=dr, pd_location=None, objective=objective,
                         entry=None, stop=getattr(sw, "extreme", None),
                         target=getattr(objective, "price", None), rr=None, rr_quality=None,
                         actionable=False, passed=False, reasons=rec_reasons, setup=None,
                         session=sess, killzone=kz,
                         context_label=context_label(direction, _bias),
                         amd_phase=amd_phase(direction, _bias, getattr(mss, "state", "")),
                         structure="forming", filters=[], recommendation=rec, tf=tf)

    cands = []
    for r in ms.ranked_sweeps:                                           # anchor: the manipulation
        sw = r.item
        direction = "long" if sw.direction == "bullish" else "short"     # sell-side raid → long, buy-side → short
        disps = disp_by_sweep.get(sw.id, [])
        disp = disps[0] if disps else None                               # best-ranked displacement off it
        mss = (mss_by_disp.get(disp.id, []) or [None])[0] if disp is not None else None
        dr = context.dealing_range if context else None
        # ERL/IRL-aware draw (Lesson 10): class first (ERL taken → seek IRL; IRL rebalanced → seek ERL),
        # then the objective inside that class. The ERL branch returns the same opposing pool as before;
        # the IRL branch lets an unfilled internal FVG become the objective when no external pool remains.
        objective = align.next_draw(context, direction, internal_arrays=stage_unfilled) if context else None

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
            mvok, mvreason = EM.validate(entry.model, entry, geom, context)   # model-specific validation
            sess, kz = session_of(getattr(sw, "time", None))
            _bias = context.bias if context else ""

            # (1) STRUCTURE — is there a valid ICT setup? The chain is complete here (entry exists);
            #     a setup is INVALID only on a structural fault — a mitigated/invalidated entry, a
            #     model-specific validation failure, a degenerate stop, or bad geometry. RR, HTF bias
            #     and premium/discount are NOT structural (a missing target is a filter concern, not
            #     invalidity — see `_reject_is_structural`).
            if entry.state in ("completed", "rejected"):
                structure, struct_reason = "invalid", f"entry {entry.lifecycle or entry.state} — no valid entry"
            elif not mvok:
                structure, struct_reason = "invalid", (mvreason or "invalid entry")
            elif geom["reject"] and _reject_is_structural(geom["reject"]):
                structure, struct_reason = "invalid", geom["reject"]
            else:
                structure, struct_reason = "valid", ""

            # (2) QUALITY — measured, NEVER gating: RR grade, HTF alignment, premium/discount, AMD
            #     phase, and the pullback depth of the entry into the leg (Lesson 8: ≥50% is adequate)
            entry.quality = quality
            pd = context.zone(E) if context else None
            clabel = context_label(direction, _bias)
            phase = amd_phase(direction, _bias, getattr(mss, "state", ""))
            pb = pullback_pct(disp, E)
            # CONTEXT ROLE of the entry PD array (Lessons 10-12): timeframe + dealing-range position
            # (not lifecycle) decide whether this FVG is an ENTRY (LTF, retrace zone), a reaction
            # (S/R confluence), or a draw. SURFACED only — enabling role=entry as a take/skip gate is a
            # separate reviewed decision, so this never changes structure/recommendation here.
            earr = (PD.role_of(PD.from_fvg(entry.source, tf), direction=direction, zone=pd,
                               erl_irl=(context.erl_irl(E) if context else None))
                    if getattr(entry, "source", None) is not None else None)
            erole = earr.role if earr is not None else ""
            erole_basis = earr.role_basis if earr is not None else {}

            # (3) COURSE FILTERS — course execution rules (≥3R, killzone, …); only for a valid structure
            filters = REC.evaluate_filters(rr=rr, killzone=kz, cfg=filters_cfg) if structure == "valid" else []

            # (4) RECOMMENDATION — TAKE / SKIP / WATCH. `entry_live` = the entry FVG has been retraced
            #     into (common state 'valid' = touched); an unfilled FVG is ARMED → WATCH (awaiting retrace).
            entry_live = (entry.state == "valid")
            rec, reasons = REC.recommend(structure=structure, structure_reason=struct_reason,
                                         filters=filters, entry_live=entry_live)
            entry.reason = reasons[0] if reasons else ""

            # legacy fields, DERIVED from the layers:
            #   actionable = a valid ICT setup exists (RR/bias/P/D no longer gate it);
            #   passed = CASCADE ELIGIBILITY — valid structure + all course filters pass (an armed,
            #     filter-passing setup is eligible to promote so a lower TF can trigger the retrace);
            #     this is DECOUPLED from TAKE, which additionally requires the entry to be live.
            actionable = (structure == "valid")
            passed = (structure == "valid" and all(f["ok"] for f in filters if not f.get("disabled")))
            status = "passed" if rec == "TAKE" else ("incomplete" if rec == "WATCH" else "rejected")
            state = "actionable" if actionable else "entry"
            checks = structural_checks(sw, disp, mss, entry, structure, struct_reason)

            setup_ns = SimpleNamespace(id=entry.id, direction=direction, entry=E, stop=S, target=tgt,
                                       rr=rr, actionable=actionable, reject_reason=(struct_reason or geom["reject"]),
                                       depends_on=(entry.id,))
            cands.append(Candidate(direction=direction, state=state, status=status, checks=checks,
                                   sweep=sw, displacement=disp, mss=mss,
                                   entry_model=entry.model, entry_obj=entry,
                                   dealing_range=dr, pd_location=pd,
                                   objective=obj, entry=E, stop=S, target=tgt, rr=rr, rr_quality=quality,
                                   actionable=actionable, passed=passed, reasons=reasons, setup=setup_ns,
                                   session=sess, killzone=kz, context_label=clabel, amd_phase=phase,
                                   pullback=pb, fvg_tiebreak=getattr(entry, "tiebreak_n", 0),
                                   entry_role=erole, entry_role_basis=erole_basis, tf=tf,
                                   structure=structure, filters=filters, recommendation=rec))

    # DE-DUPLICATE identical setups. Equal-high/low sweeps at the SAME bar/level (e.g. SWP17H@82 and
    # SWP29H@82 at the same extreme) share one displacement → one FVG → the SAME trade idea emitted
    # twice. Collapse candidates whose resulting trade is identical (direction + entry + stop + target
    # + model), keeping the first (best-ranked) — an EXACT-match dedup, no tolerance (that near-equal
    # clustering is the deferred equal-H/L parameter item, §3). Partials collapse by (dir, extreme, draw).
    def _px(x):
        return None if x is None else round(float(x), 2)
    seen, out = set(), []
    for c in cands:
        key = (c.direction, _px(c.entry), _px(c.stop), _px(c.target), c.entry_model)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


MIN_TARGET_RR = 2.0   # [COURSE] the trade's target must clear at least this reward:risk (~2R, verbally
#                       taught; confirmed by the methodology owner 2026-09-04 as course, not a [RES] knob)
# NOTE: a STALE_PROGRESS=0.5 rule (invalidate once price ran >=50% entry→target) was REMOVED 2026-09-04 —
# the raw course gives no basis for it (Lesson 8's >=50% governs retrace DEPTH, not run toward target).
# Setup invalidation is now beyond-stop only (an execution-validity rule); a missed entry keeps the setup
# and scenario alive to re-form. See execution_for_scenario.


def _pick_target(direction, entry, risk, objectives, draw_px, min_rr, price_dp=2):
    """Pick the trade's TARGET = the FIRST (nearest) opposing liquidity that clears >= `min_rr`
    reward:risk. Scans ALL liquidity objectives (4H/1H + nearer 15m) and takes the CLOSEST one beyond
    min_rr*risk — not the far thesis draw. The scenario's own draw is only a fallback when the objective
    set is empty. Returns (target, rr), or (None, None) if nothing clears the floor (setup skipped)."""
    if not risk or risk <= 0:
        return None, None
    need = min_rr * risk

    def on_side(px):
        return px is not None and (px > entry if direction == "long" else px < entry)

    opp = [o.price for o in (objectives or [])
           if on_side(getattr(o, "price", None))
           and getattr(o, "status", "") not in ("swept", "mitigated")
           and abs(o.price - entry) >= need]
    if opp:
        tgt = min(opp, key=lambda p: abs(p - entry))           # the FIRST liquidity past >=2R
        return round(tgt, price_dp), round(abs(tgt - entry) / risk, 2)
    if on_side(draw_px) and abs(draw_px - entry) >= need:      # fallback: the thesis draw (no objectives)
        return draw_px, round(abs(draw_px - entry) / risk, 2)
    return None, None


# ---- FAITHFUL execution model (reconstructed from the RAW course, Lessons 5-16, locked 2026-09-04) ---
# The course teaches ONE causal lower-timeframe sequence. No single element is sufficient; the pieces
# must be CAUSALLY chained, not merely co-located in the same premium/discount half:
#   C1 THESIS   — the active scenario (direction + dealing range + draw).                     [HTF, given]
#   C2 WHERE    — an actual INTERACTION at a liquidity location in the correct P/D half: a     (Lessons
#                 manipulation SWEEP (raid + rejection), NOT proximity to a level.              6/16)
#   C3 WHEN     — a CONFIRMED MSS on the SAME chain (sweep->displacement->MSS): the structure   (Lessons
#                 shift ORIGINATES from that interaction. Causal, not "an MSS somewhere in the   15/16)
#                 half."
#   C4 RETRACE  — the confirming leg = displacement (manip extreme -> impulse extreme) has      (Lessons
#                 retraced >= 50% (Lesson 8 pullback DEPTH) AND the entry is on the correct P/D   8/9)
#                 side of the H4/H1 range (Lesson 9). BOTH — two distinct 50%s. NOT 0.62/0.79.
#   C5 EXECUTE  — entry = a same-leg FVG CE if one sits in the C4 zone (confluence/sharper),    (Lessons
#                 ELSE the shallowest valid >=50%+P/D level. NEVER the swept WHERE reference.     8/12)
#                 FVG is OPTIONAL (no FVG != no trade). Stop beyond the manipulation extreme;
#                 target = the nearest opposing draw >= min_rr (2R [COURSE]).
# WHERE answers where, WHEN answers when, and the ENTRY is the post-confirmation retrace — three linked
# facts on ONE chain. Ambiguity (several chains/FVGs) is EXPOSED in `audit`, never resolved by "nearest"
# or an invented hierarchy. Every decision lands in `audit` for a course check (like role_of).
_ARRAY_ACTIVE = ("unswept", "unfilled", "open", "touched", "")   # not swept / mitigated / closed-spent


def _trend_sequence(structural, mss, direction):
    """LESSON-15 SEQUENCE classifier as an ORDERED, STATEFUL progression (re-derived per 15m close from the
    full skeleton, not a local point-in-time reconstruction — see ict_v2/docs/V2_GAP.md). A 15m MSS breaking
    the LAST OPPOSING structural swing is a *reversal* only if the Lesson-15 sequence formed AND remained
    valid up to the break. Reads the alternating 15m skeleton (HH/HL/LH/LL) and returns (kind, detail):

      SHORT (bearish MSS breaks a LOW):
        'reversal'     — an established UPTREND (rising highs AND rising lows into the broken low), then a
                         LOWER HIGH (failed new HH = the potential) formed STRICTLY AFTER the broken HL, and
                         no new HIGHER HIGH (beyond the prior high S[k-1]) resumed the uptrend before the
                         break. (confirmed vs potential is decided by mss.state in _structural_reversal.)
        'invalidated'  — a valid potential formed but the PRIOR TREND RESUMED: a new structural extreme beyond
                         S[k-1] (short: a high > prior high; long: a low < prior low) printed between the
                         failed-continuation pivot and the break (Lesson 15: the trend 'did not stop').
        'degenerate'   — the failed-continuation pivot and the broken swing are the SAME 15m bar/index: no
                         temporal LH→HL→break ordering exists (a wide outside-bar artifact, not a sequence).
        'premature'    — the last opposing swing is broken but NO qualifying failed-continuation pivot first.
        'continuation' — the prior trend ran the SAME way as the break.
      LONG mirrors it (downtrend -> higher-low, strictly after -> break last LH -> HH; resumed = a new lower
      low below the prior low). 'none' — the skeleton is not the expected alternating shape / too short.

    The invalidation reference is the PRIOR structural extreme S[k-1] (a new falling low/rising high = a
    confirmed structural swing beyond it), NOT the failed-continuation pivot itself; the survival scan stops
    at the confirmation break (mss.confirm_index)."""
    if not structural or mss is None:
        return ("none", {})
    bi = getattr(mss, "broken_index", None)
    k = next((j for j, s in enumerate(structural)
              if getattr(s, "index", None) == bi), None)
    if k is None or k < 3:                                          # need H1,L1,H2,L2 (…,k-3,k-2,k-1,k)
        return ("none", {})
    n = len(structural)
    px = lambda j: float(getattr(structural[j], "price"))
    kd = lambda j: getattr(structural[j], "kind", "")
    brk = getattr(mss, "confirm_index", None)                      # the confirmation break bar (None if wick-only)

    def _ordered_and_survived(ref, resume_kind):
        """(state, resume) for a would-be reversal whose failed-continuation pivot is structural[k+1]:
        'degenerate' if that pivot is NOT strictly after the broken swing (same bar); 'invalidated' if the
        prior trend RESUMED (a structural `resume_kind` swing beyond `ref`) between the pivot and the break;
        else 'reversal'. `resume` is the first resuming extreme (for the audit) or None."""
        kp1_idx = getattr(structural[k + 1], "index", None)
        if kp1_idx is None or kp1_idx <= bi:                       # strict temporal ordering (pivot after break-target)
            return ("degenerate", None)
        for s in structural:                                       # potential-survival scan, bounded by the break
            sidx = getattr(s, "index", None)
            if sidx is None or sidx <= kp1_idx:
                continue
            if brk is not None and sidx > brk:
                break
            if getattr(s, "kind", "") == resume_kind:
                p = float(getattr(s, "price"))
                if (p > ref) if resume_kind == "high" else (p < ref):
                    return ("invalidated", round(p, 2))
        return ("reversal", None)

    if direction == "short":                                       # bearish MSS breaks a LOW (uptrend's HL)
        if not (kd(k) == "low" and kd(k - 1) == "high" and kd(k - 2) == "low" and kd(k - 3) == "high"):
            return ("none", {})
        L1, L2, H1, H2 = px(k - 2), px(k), px(k - 3), px(k - 1)
        prior_up = (L1 < L2) and (H1 < H2)                         # rising lows AND rising highs (HH/HL)
        prior_down = (L1 > L2) and (H1 > H2)
        H3 = px(k + 1) if (k + 1 < n and kd(k + 1) == "high") else None
        failed_lh = (H3 is not None and H3 <= H2)                  # the high after the HL is a LOWER high
        detail = {"prior_trend": ("up (HH/HL)" if prior_up else "down (LH/LL)" if prior_down else "sideways"),
                  "failed_continuation_pivot": (f"LH {round(H3, 2)}" if failed_lh else None),   # only when it qualifies
                  "prior_opposing_extreme": round(H2, 2),          # the prior structural HIGH the LH failed
                  "last_structural_swing": round(L2, 2),           # the HL that gets broken -> LL
                  "break_kind": "LL"}
        if prior_up and failed_lh:
            state, resume = _ordered_and_survived(H2, "high")      # resumed = a new HIGHER HIGH above the prior high
            if resume is not None:
                detail = {**detail, "resumed_beyond_prior_extreme": resume}
            return (state, detail)
        if prior_up:
            return ("premature", detail)                           # HL broken with no LH first
        if prior_down:
            return ("continuation", detail)
        return ("none", detail)
    # long: bullish MSS breaks a HIGH (the downtrend's last LH)
    if not (kd(k) == "high" and kd(k - 1) == "low" and kd(k - 2) == "high" and kd(k - 3) == "low"):
        return ("none", {})
    H1, H2, L1, L2 = px(k - 2), px(k), px(k - 3), px(k - 1)
    prior_down = (H1 > H2) and (L1 > L2)                           # falling highs AND falling lows (LH/LL)
    prior_up = (H1 < H2) and (L1 < L2)
    L3 = px(k + 1) if (k + 1 < n and kd(k + 1) == "low") else None
    failed_hl = (L3 is not None and L3 >= L2)                      # the low after the LH is a HIGHER low
    detail = {"prior_trend": ("down (LH/LL)" if prior_down else "up (HH/HL)" if prior_up else "sideways"),
              "failed_continuation_pivot": (f"HL {round(L3, 2)}" if failed_hl else None),        # only when it qualifies
              "prior_opposing_extreme": round(L2, 2),              # the prior structural LOW the HL failed
              "last_structural_swing": round(H2, 2),               # the LH that gets broken -> HH
              "break_kind": "HH"}
    if prior_down and failed_hl:
        state, resume = _ordered_and_survived(L2, "low")           # resumed = a new LOWER LOW below the prior low
        if resume is not None:
            detail = {**detail, "resumed_beyond_prior_extreme": resume}
        return (state, detail)
    if prior_down:
        return ("premature", detail)                              # LH broken with no HL first
    if prior_up:
        return ("continuation", detail)
    return ("none", detail)


def _structural_reversal(confirm_ms, direction, zone, price_dp):
    """C2/C3 — the STRUCTURAL trend change on the HIGHER structural timeframe (the 15m confirm read; the
    course's floor is '>=15m', Lesson 6). A 15m MSS breaks the LAST OPPOSING 15m STRUCTURAL swing — a
    swing that is meaningful BY SCALE (a 15m structural high/low), NOT an arbitrary 1m local pivot. This
    replaces the retired 1m-MSS + dominant/protected gate: the source-derived structural hierarchy (which
    swing, at which scale, in which sequence) decides validity, per the forensic finding that the 1m-local
    `dominant/protected` flag is not the course's definition of a meaningful swing.

    Returns the WHERE (the 15m manipulation/sweep, in the correct premium/discount half), the confirming
    reversal LEG (the 15m displacement start->end), the broken 15m structural swing, any same-leg 15m FVG,
    and the state: `confirmed` (a 15m body close broke the last opposing structural swing = LL/HH) or
    `potential` (a 15m candidate MSS, wick penetration only = a Lower-High/Higher-Low forming). None if no
    reversal in `direction` has its manipulation in the half. `dominant/protected` are surfaced as METADATA
    only (never the gate). Short = bearish 15m MSS (break last structural HL -> LL); long = bullish (HH).
    The 1m layer only TIMES the retrace fill; it never redefines a 15m structural swing (no look-ahead:
    the 15m structure is the last CLOSED 15m read, fixed between 15m closes)."""
    if confirm_ms is None:
        return None
    lo, hi = zone
    want = "bullish" if direction == "long" else "bearish"
    disp_by_id = {d.item.id: d.item for d in getattr(confirm_ms, "ranked_displacements", [])}
    sweep_by_id = {s.item.id: s.item for s in getattr(confirm_ms, "ranked_sweeps", [])}
    flag = {}                                                   # swing index -> (dominant, protected) — metadata only
    for cs in (getattr(confirm_ms, "classified", None) or []):
        sw2 = getattr(cs, "swing", None)
        if sw2 is not None:
            flag[getattr(sw2, "index", None)] = (bool(getattr(cs, "dominant", False)),
                                                 bool(getattr(cs, "protected", False)))
    # LESSON-15 SEQUENCE GATE. A direction-matching 15m MSS breaking the last opposing swing is only a
    # REVERSAL if the Lesson-15 sequence preceded the break (prior opposite trend -> failed-continuation
    # pivot -> break the last structural swing). Otherwise it is a CONTINUATION or a PREMATURE break, never
    # a confirmed structural reversal. The skeleton is the 15m structural read; if it is unavailable (e.g. a
    # unit fixture that supplies no skeleton) the sequence cannot be judged and the break is taken at face
    # value (live always carries a structural skeleton, so the gate is always active in the engine).
    structural = getattr(confirm_ms, "structural", None) or []
    best_conf, best_pot, non_rev = None, None, None
    for r in getattr(confirm_ms, "ranked_mss", []):
        m = r.item
        if getattr(m, "direction", "") != want:
            continue
        d = disp_by_id.get(m.depends_on[0]) if getattr(m, "depends_on", None) else None
        if d is None:
            continue
        sw = sweep_by_id.get(d.depends_on[0]) if getattr(d, "depends_on", None) else None
        manip = getattr(sw, "extreme", None)
        if manip is None:
            manip = getattr(d, "start_price", None)
        if manip is None or not (lo <= float(manip) <= hi):     # WHERE must interact inside the correct half
            continue
        if structural:
            seq_kind, seq_detail = _trend_sequence(structural, m, direction)
        else:
            seq_kind, seq_detail = ("reversal", {"prior_trend": "(no 15m skeleton in this read)",
                                                 "failed_continuation_pivot": None,
                                                 "last_structural_swing": round(float(getattr(m, "broken_price", 0.0)), price_dp),
                                                 "break_kind": ("HH" if direction == "long" else "LL")})
        rec = (m, d, sw, float(manip), seq_detail)
        if seq_kind != "reversal":                              # continuation / premature / none — NOT a reversal
            if non_rev is None:
                non_rev = (m, d, sw, float(manip), seq_kind, seq_detail)
            continue
        st = getattr(m, "state", "")
        if st == "confirmed":                                  # body close broke the last structural swing = LL/HH
            if best_conf is None or (getattr(m, "confirm_index", -1) or -1) > (getattr(best_conf[0], "confirm_index", -1) or -1):
                best_conf = rec
        elif st in ("candidate", "potential") and best_pot is None:
            best_pot = rec                                     # LH/HL formed, break not a body close yet -> potential
    chosen = best_conf or best_pot
    if chosen is None:
        # No Lesson-15 reversal. If a break was seen but classified continuation/premature, surface WHY so
        # the audit shows the engine detected a 15m break and rejected it as not-a-reversal (WATCHING).
        if non_rev is not None:
            m, d, sw, manip, seq_kind, seq_detail = non_rev
            return {"state": "non-reversal", "classification": seq_kind, "seq": seq_detail, "manip": manip,
                    "broken_price": round(float(getattr(m, "broken_price", 0.0)), price_dp),
                    "mss_id": getattr(m, "id", "")}
        return None
    m, d, sw, manip, seq_detail = chosen
    dom, prot = flag.get(getattr(m, "broken_index", None), (False, False))
    did = getattr(d, "id", None)
    fvgs = [r.item for r in getattr(confirm_ms, "ranked_fvgs", [])
            if getattr(r.item, "depends_on", None) and r.item.depends_on[0] == did
            and getattr(r.item, "status", "") != "mitigated"]
    return {"state": ("confirmed" if best_conf else "potential"), "manip": manip,
            "leg_a": float(getattr(d, "start_price", manip)), "leg_b": float(getattr(d, "end_price", manip)),
            "broken_price": round(float(getattr(m, "broken_price", 0.0)), price_dp),
            "broken_dominant": dom, "broken_protected": prot, "seq": seq_detail, "classification": "reversal",
            "pool": float(getattr(sw, "pool_price", manip)) if sw is not None else float(manip),
            "mss_id": getattr(m, "id", ""), "fvgs": fvgs}


def _retrace_entry(chain, direction, ce, price_dp):
    """C4/C5 — from a CONFIRMED chain, the reversal leg and the post-confirmation entry. The leg is the
    displacement (manipulation extreme -> impulse extreme). The valid entry band is the >=50% retrace of
    that leg (Lesson 8, depth) INTERSECTED with the dealing-range discount/premium half (Lesson 9). Entry
    = a same-leg FVG CE if it lands in that band (confluence/sharper), ELSE the shallowest valid level
    (the leg 50% or the equilibrium edge, whichever is deeper) — never the swept WHERE reference, and never
    forced to 0.62/0.79. Returns (entry, stop, leg_low, leg_high, fvg_used) or None if the >=50%+P/D
    intersection is empty (the leg does not retrace into the correct half)."""
    a, b = chain["leg_a"], chain["leg_b"]
    if a is None or b is None:
        return None
    leg_low, leg_high = (a, b) if a <= b else (b, a)
    leg_mid = (leg_low + leg_high) / 2.0                         # the 50% retrace level (Lesson 8)
    manip = chain["manip"]
    if direction == "long":
        # >=50% retrace DOWN into the leg = price <= leg_mid; discount = <= ce. Deepest valid = leg_low.
        ceiling = min(leg_mid, ce)                              # shallowest price that is BOTH >=50% and discount
        floor = leg_low
        if ceiling <= floor:
            return None                                         # leg does not retrace into the discount
        fvg = next((f for f in chain["fvgs"]
                    if getattr(f, "direction", "") == "bullish"
                    and floor <= float(getattr(f, "ce", 1e18)) <= ceiling), None)
        entry = float(getattr(fvg, "ce")) if fvg is not None else ceiling
        stop = min(manip, leg_low)                              # beyond the manipulation extreme (below)
    else:
        floor = max(leg_mid, ce)                                # shallowest price that is BOTH >=50% and premium
        ceiling = leg_high
        if ceiling <= floor:
            return None
        fvg = next((f for f in chain["fvgs"]
                    if getattr(f, "direction", "") == "bearish"
                    and floor <= float(getattr(f, "ce", -1e18)) <= ceiling), None)
        entry = float(getattr(fvg, "ce")) if fvg is not None else floor
        stop = max(manip, leg_high)                             # beyond the manipulation extreme (above)
    return (round(entry, price_dp), round(stop, price_dp), round(leg_low, price_dp),
            round(leg_high, price_dp), fvg)


def _confluence_in(objectives, direction, lo, hi, price_dp):
    """Course-taught PD arrays (FVG/fib/EQH-EQL/NWOG/ORG) whose reference sits in [lo, hi] — SURFACED as
    confluence only (Lesson 14: overlapping arrays strengthen a location). Never selects the entry."""
    active = _ARRAY_ACTIVE
    out = []
    for o in (objectives or []):
        k = getattr(o, "kind", None)
        p = getattr(o, "price", None)
        if k in ("fvg", "eqhl", "nwog", "org", "fib") and getattr(o, "status", "") in active \
                and p is not None and lo <= float(p) <= hi:
            out.append({"kind": k, "label": getattr(o, "label", k), "price": round(float(p), price_dp)})
    return out


def execution_for_scenario(scenario, candidates=None, price=None, objectives=None, ms=None,
                           confirm_ms=None, min_stop: float | None = None,
                           min_rr: float = MIN_TARGET_RR, price_dp: int = 2, reversals=None) -> dict | None:
    """FAITHFUL execution decision for one active SCENARIO. WHAT-corrected model (2026-09): the WHEN is a
    STRUCTURAL trend change on the HIGHER structural timeframe (the 15m `confirm_ms`), NOT a 1m-local MSS.
      C2 WHERE — a 15m manipulation/sweep in the correct premium/discount half.
      C3 WHEN  — a CONFIRMED 15m structural reversal in the scenario direction: a 15m body close broke the
                 LAST OPPOSING 15m structural swing (short: prior up-structure -> LH -> break last HL -> LL;
                 long: prior down-structure -> HL -> break last LH -> HH). A 15m *potential* (candidate MSS,
                 wick only) is WATCHING, not confirmed. `dominant/protected` are metadata, never the gate.
      C4 RETRACE — the 15m reversal leg retraced >=50% AND the entry is on the correct P/D side.
      C5 EXECUTE — entry = the >=50% retrace level (same-leg 15m FVG if it overlaps, else the leg level;
                 FVG optional), stop beyond the 15m manipulation extreme, target opposing draw >= min_rr.
    The 1m layer only TIMES the fill (the reachability gate in ScenarioBook.monitor). `candidates`/`ms`
    (the 1m read) are accepted for signature compatibility but no longer define the structural swing."""
    dirn = scenario.direction
    zone = getattr(scenario, "entry_zone", None)
    if zone is None or price is None:
        return None
    lo, hi = (zone[0], zone[1]) if zone[0] <= zone[1] else (zone[1], zone[0])
    ce = hi if dirn == "long" else lo                            # dealing-range equilibrium (edge of the half)
    pd_zone = "discount" if dirn == "long" else "premium"
    new_struct = "HH/HL" if dirn == "long" else "LH/LL"

    # C2/C3 — the STRUCTURAL reversal on the 5m (the WHEN, at the Lesson-15 scale; 1m does not define it).
    # PERSISTENT source of truth: the ReversalBook (Lesson-15 POTENTIAL carried across 5m closes). The
    # stateless per-close `_structural_reversal(confirm_ms, ...)` is retained only as a fallback for the
    # non-reversal audit (continuation/premature/invalidated/degenerate) and for unit fixtures that pass a
    # confirm_ms with no book. The book NEVER re-anchors S[k]/S[k-1]; downstream never gates its existence.
    if reversals is not None:
        R = reversals.for_scenario(dirn, lo, hi)
        if R is None:
            R = _structural_reversal(confirm_ms, dirn, (lo, hi), price_dp)   # non-reversal audit only
            if R is not None and R.get("state") == "confirmed":
                R = None                                                    # confirmed is owned by the book
    else:
        R = _structural_reversal(confirm_ms, dirn, (lo, hi), price_dp)

    def _audit(state, **extra):
        a = {"pd_zone": pd_zone, "pd_zone_range": [round(lo, price_dp), round(hi, price_dp)],
             "structural_tf": "5m",
             "conditions": {"C2_where_sweep": bool(R),
                            "C3_confirmed_structural_reversal": bool(R and R["state"] == "confirmed"),
                            "C4_retrace_50_and_pd": None, "C5_geometry": None}, "state": state}
        a.update(extra)
        return a

    # C2 fails — no 15m manipulation/reversal in `dirn` interacted with the correct half yet.
    if R is None:
        return None                                                        # watching

    _blank = {"state": "watching", "entry": None, "stop": None, "target": None, "rr": None,
              "order": None, "sl_order": None, "tp_order": None, "fvg_top": None, "fvg_bottom": None,
              "entry_model": "", "usable_models": [], "price": round(price, price_dp),
              "dist_to_entry": None, "entry_role": "", "tf": getattr(scenario, "tf", "")}

    # LESSON-15 SEQUENCE: a 15m break was seen in the correct half but it was NOT a Lesson-15 reversal.
    #   continuation — the break runs WITH the prior trend.
    #   premature    — the last opposing swing broke with no qualifying failed-continuation pivot first.
    #   invalidated  — a valid potential formed but the prior trend RESUMED (a new structural extreme beyond
    #                  S[k-1]) before confirmation → the potential is cancelled (Lesson 15: it 'did not stop').
    #   degenerate   — the failed-continuation pivot and the broken swing are the same 15m bar (no ordering).
    # Every one is WATCHING, no order; the audit carries the classification and (for invalidated) the extreme.
    if R["state"] == "non-reversal":
        cls = R.get("classification", "non-reversal")
        seq = R.get("seq", {}) or {}
        rstate = {"invalidated": "cancelled", "degenerate": "degenerate"}.get(cls, "none")
        resumed = seq.get("resumed_beyond_prior_extreme")
        if cls == "invalidated":
            why = (f"POTENTIAL {new_struct} reversal CANCELLED — the prior trend ({seq.get('prior_trend', '?')}) "
                   f"resumed with a new structural extreme ({resumed}) beyond its prior extreme "
                   f"({seq.get('prior_opposing_extreme')}) before the break of {R['broken_price']}; per Lesson 15 "
                   f"the trend did not stop, so there is no reversal - no order")
        elif cls == "degenerate":
            why = (f"a 15m break of {R['broken_price']} is DEGENERATE — its failed-continuation pivot and the "
                   f"broken swing are the same 15m bar (no LH→HL→break ordering); not a Lesson-15 sequence - no order")
        else:
            why = (f"a 15m break was detected (broke {R['broken_price']}) but classified {cls.upper()}, not a "
                   f"Lesson-15 reversal (prior trend {seq.get('prior_trend', '?')}, "
                   f"failed-continuation pivot {seq.get('failed_continuation_pivot') or 'none'}); no trend change - no order")
        return {**_blank, "why": why,
                "audit": _audit("watching", reason=f"15m break classified {cls} (not a Lesson-15 reversal)",
                                when={"mss_id": R["mss_id"], "broken_swing": R["broken_price"],
                                      "classification": cls, "reversal_state": rstate,
                                      "prior_trend": seq.get("prior_trend"),
                                      "failed_continuation_pivot": seq.get("failed_continuation_pivot"),
                                      "resumed_beyond_prior_extreme": resumed,
                                      "last_structural_swing": seq.get("last_structural_swing"),
                                      "confirmation_break": None})}

    # C3 fails — a 15m reversal is only POTENTIAL (candidate MSS / wick), not a CONFIRMED structural trend
    # change (no 15m body close through the last opposing structural swing yet). FORMING: no entry/stop/
    # target, NOT actionable — WATCHING, no alert. Becomes armed only once the 15m reversal confirms.
    if R["state"] != "confirmed":
        seq = R.get("seq", {}) or {}
        why = (f"POTENTIAL {new_struct} reversal on the 15m (manip {round(R['manip'], price_dp)} in the "
               f"{pd_zone}); Lesson-15 sequence present (prior trend {seq.get('prior_trend', '?')}, "
               f"failed-continuation pivot {seq.get('failed_continuation_pivot') or 'none'}) but awaiting a "
               f"CONFIRMED body-close break of the last 15m structural swing (Lessons 6/15) - no order yet")
        return {**_blank, "why": why,
                "audit": _audit("watching", reason="15m reversal only potential (not confirmed)",
                                when={"mss_id": R["mss_id"], "broken_swing": R["broken_price"],
                                      "classification": "reversal", "reversal_state": "potential",
                                      "prior_trend": seq.get("prior_trend"),
                                      "failed_continuation_pivot": seq.get("failed_continuation_pivot"),
                                      "last_structural_swing": seq.get("last_structural_swing"),
                                      "locality_reject": R.get("locality_reject"),   # why the break hasn't confirmed
                                      "confirmation_break": None})}

    # C4/C5 — the confirmed 15m reversal's leg + retrace entry (structural leg; 1m only fills it).
    ch = R
    geom = _retrace_entry(ch, dirn, ce, price_dp)
    if geom is None:
        # C4 empty — the confirming leg does not retrace >=50% into the correct P/D half.
        return {"state": "retracing", "entry": None, "stop": None, "target": None, "rr": None,
                "price": round(price, price_dp),
                "why": (f"reversal confirmed (leg {round(ch['leg_a'] or 0, price_dp)}-"
                        f"{round(ch['leg_b'] or 0, price_dp)}) but its >=50% retrace does not reach the "
                        f"{pd_zone} half - not a valid execution zone (Lessons 8/9)"),
                "audit": _audit("retracing", reason="leg 50% retrace not in the P/D half",
                                **{"conditions": {"C2_where_sweep": True, "C3_confirmed_structural_reversal": True,
                                                  "C4_retrace_50_and_pd": False, "C5_geometry": None}})}
    entry, stop, leg_low, leg_high, fvg = geom
    risk = abs(entry - stop)
    fvg_used = fvg is not None
    # DEGENERATE-STOP REJECTION (spec §15 / Lesson 15): reject a near-zero risk rather than execute it.
    if min_stop and risk < min_stop:
        return {"state": "retracing", "entry": entry, "stop": stop, "target": None, "rr": None,
                "price": round(price, price_dp),
                "why": (f"degenerate stop - risk {round(risk, price_dp)} < execution floor {min_stop:g} "
                        f"(setup rejected, Lesson 15)"),
                "audit": _audit("retracing", reason="degenerate stop")}
    draw_px = getattr(getattr(scenario, "draw", None), "price", None)
    tgt, rr = _pick_target(dirn, entry, risk, objectives, draw_px, min_rr, price_dp)      # opposing draw >= 2R
    gap = round(entry - price, price_dp)
    fvg_top = round(float(getattr(fvg, "top")), price_dp) if fvg_used else None
    fvg_bottom = round(float(getattr(fvg, "bottom")), price_dp) if fvg_used else None
    confluence = _confluence_in(objectives, dirn, min(leg_low, entry), max(leg_high, entry), price_dp)
    if dirn == "long":
        order = "BUY STOP" if price < entry else "BUY LIMIT"
    else:
        order = "SELL STOP" if price > entry else "SELL LIMIT"

    base = {"entry": entry, "stop": stop, "target": tgt, "rr": rr, "order": order,
            "sl_order": ("sell stop" if dirn == "long" else "buy stop"),
            "tp_order": ("sell limit" if dirn == "long" else "buy limit"),
            "fvg_top": fvg_top, "fvg_bottom": fvg_bottom,
            "entry_model": ("fvg" if fvg_used else "retrace"),   # fvg = confluence-sharpened; retrace = leg >=50% (no FVG)
            "usable_models": sorted({c["kind"] for c in confluence}),
            "price": round(price, price_dp), "dist_to_entry": gap,
            "entry_role": "entry", "tf": getattr(scenario, "tf", "")}

    def _full_audit(state):
        meta = ("dominant" if ch["broken_dominant"] else "protected" if ch["broken_protected"] else "not-flagged")
        return _audit(state, why_accepted=(why if state == "triggered" else ""),
                      where={"pool": round(ch["pool"], price_dp), "manip": round(ch["manip"], price_dp)},
                      when={"mss_id": ch["mss_id"], "kind": new_struct, "broken_swing": ch["broken_price"],
                            "broken_dominant": ch["broken_dominant"], "broken_protected": ch["broken_protected"],
                            "accepted": True, "classification": "reversal", "reversal_state": "confirmed",
                            "prior_trend": (ch.get("seq") or {}).get("prior_trend"),
                            "failed_continuation_pivot": (ch.get("seq") or {}).get("failed_continuation_pivot"),
                            "last_structural_swing": (ch.get("seq") or {}).get("last_structural_swing"),
                            "confirmation_break": ch["broken_price"], "locality": ch.get("locality"),
                            "rule": "confirmed 15m structural reversal — an established opposite trend, then a "
                                    "failed-continuation pivot (LH/HL), then a body close broke the last "
                                    "opposing 15m STRUCTURAL swing (Lesson-15 sequence, scale Lesson 6); "
                                    f"dominant/protected flags are metadata only (here: {meta})"},
                      reversal_leg={"low": leg_low, "high": leg_high,
                                    "mid_50pct": round((leg_low + leg_high) / 2.0, price_dp)},
                      entry_source=("same-leg FVG (confluence)" if fvg_used else ">=50% retrace level (no FVG)"),
                      confluence=confluence,
                      **{"conditions": {"C2_where_sweep": True, "C3_confirmed_structural_reversal": True,
                                        "C4_retrace_50_and_pd": True, "C5_geometry": tgt is not None}})

    # No opposing draw clears min_rr -> a real confirmed reversal but not a tradeable setup (no alert).
    if tgt is None:
        return {**base, "state": "retracing",
                "why": (f"confirmed reversal, entry {entry} in the {pd_zone}, but no opposing draw clears "
                        f"{min_rr:g}R - skipped"),
                "audit": _full_audit("retracing")}

    # TRIGGER GATE. Invalidation = beyond the stop only (execution validity; STALE_PROGRESS removed). A
    # missed entry keeps the setup and the scenario alive (per-bar; never added to _traded_setups).
    beyond_stop = (price <= stop) if dirn == "long" else (price >= stop)
    retraced_to_entry = (price <= entry) if dirn == "long" else (price >= entry)   # >=50% retrace reached
    src = "same-leg FVG" if fvg_used else ">=50% retrace"
    if beyond_stop:
        state = "stale"
        why = (f"invalidated - price {round(price, price_dp)} is beyond the stop {stop} "
               f"(setup dead this bar, not arming)")
    elif not retraced_to_entry:
        state = "armed"
        move = "fall" if dirn == "long" else "rise"
        why = (f"reversal confirmed ({new_struct}); awaiting price to {move} {abs(gap):g} pts into the "
               f"{src} entry {entry} in the {pd_zone} (now {round(price, price_dp)}); target {tgt} ({rr}R)")
    else:
        state = "triggered"
        why = (f"confirmed reversal + >=50% retrace to the {src} entry {entry} in the {pd_zone} - "
               f"WHERE+WHEN+retrace met, trigger now (target {tgt}, {rr}R)")

    return {**base, "state": state, "why": why, "audit": _full_audit(state)}


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
    all_cands = generate_candidates(ms, context, entry_models=entry_models, min_stop=min_stop, bars=bars, tf=tf)
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
        if c.actionable and c.passed:                        # eligible (valid + filters) → evaluate confirmation
            if not setup_dirs:                               # nothing gated on the higher layer to confirm
                c.reasons = list(c.reasons) + [f"Waiting for a gated {against} in this direction"]
                c.passed, c.status, c.recommendation = False, "incomplete", "WATCH"
                c.checks.append(_mk(node, "fail", "awaiting " + against, False))
            elif c.direction not in setup_dirs:              # a gated higher setup exists, opposite way
                c.reasons = list(c.reasons) + [
                    f"Direction mismatch — {tf} {c.direction} vs {against} {'/'.join(setup_dirs)}"]
                c.passed, c.status, c.recommendation = False, "rejected", "SKIP"
                c.checks.append(_mk(node, "fail", "wrong direction", True))
            else:
                c.checks.append(_mk(node, "ok"))             # confirmed — keep the generated recommendation
        else:                                                # structurally incomplete or filtered out
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
    # HTF bias is CONTEXT, not a gate (§1/§17): a neutral/absent bias no longer blocks execution —
    # TAKE is decided by structure + course filters, so the cascade can still fire counter-context.
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
