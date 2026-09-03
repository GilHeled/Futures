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


MIN_TARGET_RR = 2.0   # the trade's target must give at least this reward:risk (user rule ~2R)
# TIMELINESS: an entry is only ARMED while price is still AHEAD of the move. Once price has covered this
# fraction of the entry→target distance (the draw is nearly reached), a retrace-entry is STALE — the move
# already ran, so it is surfaced as 'stale', never armed/actionable (fixes the "armed too late" case).
STALE_PROGRESS = 0.5


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


def execution_for_scenario(scenario, candidates, price=None, objectives=None,
                           min_rr: float = MIN_TARGET_RR, price_dp: int = 2) -> dict | None:
    """Decide an active SCENARIO's execution state from the entry candidates generated on the execution
    timeframe. This is the M15/M1 job: the higher timeframes already fixed the thesis (direction, zone,
    draw); here we only watch for the entry inside the scenario's retracement zone.

    Geometry per the agreed rule: entry = the entry-role PD array's CE (the candidate's entry); stop =
    the candidate's manipulation-extreme stop; target = the SCENARIO's draw (not the candidate's own).
    States: triggered (entry retraced into) → armed (entry PD array present, awaiting retrace) →
    retracing (price in the zone, no entry array yet) → None (watching; price not in the zone)."""
    zone = getattr(scenario, "entry_zone", None)
    dirn = scenario.direction

    def in_zone(p):
        return zone is not None and p is not None and zone[0] <= p <= zone[1]

    def stop_ok(c):     # sane stop side for the direction (long: stop below entry; short: above)
        if c.entry is None or c.stop is None:
            return False
        return (c.stop < c.entry) if dirn == "long" else (c.entry < c.stop)

    def _live(c):
        return getattr(getattr(c, "entry_obj", None), "state", "") == "valid"

    usable = [c for c in candidates if c.direction == dirn and c.entry is not None and in_zone(c.entry)
              and getattr(c, "structure", "") == "valid" and stop_ok(c)]
    # FVG is no longer MANDATORY (Lessons 15/16): a LIVE non-FVG-retrace entry — a confirmed market-
    # structure reversal, which is live-on-confirmation and carries no context 'entry' role — TRIGGERS
    # first (it still passes the discount/premium zone gate a reversal-off-the-low satisfies). Otherwise
    # the FVG entry-role pool is used exactly as before (waiting → ARMED, retraced → triggered).
    entry_cands = ([c for c in usable if _live(c) and getattr(c, "entry_role", "") != "entry"]
                   or [c for c in usable if c.entry_role == "entry"] or usable)
    if entry_cands:
        c = entry_cands[0]
        risk = abs(c.stop - c.entry)
        draw_px = getattr(getattr(scenario, "draw", None), "price", None)
        tgt, rr = _pick_target(dirn, c.entry, risk, objectives, draw_px, min_rr, price_dp)  # nearest draw >= min_rr
        if tgt is not None:                                     # a target clearing >=2R exists -> tradeable
            live = getattr(getattr(c, "entry_obj", None), "state", "") == "valid"
            gap = (round(c.entry - price, price_dp) if price is not None else None)
            # how far price has already travelled from the entry toward the target (0 at entry, 1 at target)
            span = (tgt - c.entry)
            progress = ((price - c.entry) / span) if (span and price is not None) else 0.0
            # NOT ACTIONABLE (stale) in two ways, both taking precedence over a 'live' (touched) FVG:
            #   • MISSED — price has covered >= STALE_PROGRESS of the entry→target move (draw nearly reached);
            #   • INVALIDATED — price is beyond the STOP on the loss side (short: at/above stop; long:
            #     at/below stop) → the setup is dead (an entry now would be an instant stop-out).
            beyond_stop = (c.stop is not None and price is not None
                           and ((price >= c.stop) if dirn == "short" else (price <= c.stop)))
            stale = (progress >= STALE_PROGRESS) or beyond_stop
            if beyond_stop:
                why = (f"invalidated - price {round(price, price_dp)} is beyond the stop "
                       f"{round(c.stop, price_dp)} (setup dead, not arming)")
            elif stale:
                rem = round(abs(tgt - price), price_dp) if price is not None else None
                why = (f"missed - price already ran {round(progress * 100)}% from entry "
                       f"{round(c.entry, price_dp)} to target {tgt} (now {round(price, price_dp)}); "
                       f"only {rem} pts left - not arming")
            elif live:
                why = f"entry retraced into - trigger now (target {tgt}, {rr}R)"
            elif gap is not None:
                move = "rise" if gap > 0 else "fall"
                why = (f"{dirn} entry rests at {round(c.entry, price_dp)} - awaiting price to {move} "
                       f"{abs(gap):g} pts into it (now {round(price, price_dp)}); target {tgt} ({rr}R)")
            else:
                why = f"entry PD array armed - target {tgt} ({rr}R)"
            # Topstep entry order TYPE depends on where price is vs the entry (a resting order must not
            # be on the wrong side or it fills instantly at market):
            #   long  — entry below price -> BUY LIMIT ; entry above price -> BUY STOP
            #   short — entry above price -> SELL LIMIT; entry below price -> SELL STOP
            # Bracket legs are fixed: SL = stop-market the other way, TP = limit.
            if dirn == "long":
                order = "BUY STOP" if (price is not None and price < c.entry) else "BUY LIMIT"
            else:
                order = "SELL STOP" if (price is not None and price > c.entry) else "SELL LIMIT"
            _fvg = getattr(getattr(c, "entry_obj", None), "source", None)   # the v1 FVG (box bounds, for the chart)
            fvg_top = round(float(_fvg.top), price_dp) if getattr(_fvg, "top", None) is not None else None
            fvg_bottom = round(float(_fvg.bottom), price_dp) if getattr(_fvg, "bottom", None) is not None else None
            usable_models = sorted({getattr(x, "entry_model", "") for x in usable if getattr(x, "entry_model", "")})
            return {"state": ("stale" if stale else "triggered" if live else "armed"),
                    "entry": round(c.entry, price_dp), "stop": round(c.stop, price_dp), "target": tgt, "rr": rr,
                    "order": order, "sl_order": ("sell stop" if dirn == "long" else "buy stop"),
                    "tp_order": ("sell limit" if dirn == "long" else "buy limit"),
                    "fvg_top": fvg_top, "fvg_bottom": fvg_bottom,
                    "entry_model": getattr(c, "entry_model", ""),   # which model produced the entry (structure|fvg)
                    "usable_models": usable_models,                 # models that ALSO had a usable entry now
                    "price": (round(price, price_dp) if price is not None else None), "dist_to_entry": gap,
                    "entry_role": c.entry_role, "tf": getattr(c, "tf", ""), "why": why}
        if in_zone(price):
            return {"state": "retracing",
                    "why": f"entry present but no opposing draw clears {min_rr:g}R - skipped"}
    if in_zone(price):
        return {"state": "retracing",
                "why": "price retracing into the entry zone - awaiting an entry PD array"}
    return None   # watching: price not yet in the retracement zone


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
