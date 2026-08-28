"""SCENARIO LAYER — the small, stable set of market theses the engine maintains (user decision
2026-08-28).

At any moment the engine keeps only the **top 2 (max 3)** execution scenarios — the most plausible
paths the market may take from the current H4+H1 context. A Scenario is a *thesis*, not an open trade:
a direction toward a DRAW (a liquidity objective, any kind), plus the retracement ZONE an entry would
form in. M15/M1 then MONITOR these; when one's execution setup fires, that scenario is the trade.

STABILITY IS THE POINT. Scenarios are (re)built only on a CONTEXT close (H4/H1), never on an M1/M5
tick. They persist by STRUCTURAL IDENTITY (`{direction, draw level, dealing range}`), so a stable
context reproduces the SAME scenarios in place — the set changes only on a meaningful structural event
(a draw is taken, the bias confirms the other way, the dealing range is superseded, or a new stronger
draw appears). Price noise moves nothing. An asymmetric hold band (admit at top-2, drop only past top-3)
stops near-tied theses from flapping. All ranking factors are transparent (each scenario shows its why).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ict_v2 import liquidity as LQ


@dataclass
class Scenario:
    """One market thesis: direction toward a draw, with the retracement zone an entry would form in."""
    scenario_id: str                       # stable structural key {direction:draw-kind:level:range}
    direction: str                         # long | short
    draw: LQ.LiquidityObjective            # the target objective (any kind)
    entry_zone: tuple                      # (low, high) — discount (long) / premium (short) retracement
    basis: dict = field(default_factory=dict)   # {bias, intraday_direction, range_key, why}
    rank: int = 0                          # 0 = most plausible
    rank_factors: dict = field(default_factory=dict)
    state: str = "watching"                # watching | retracing | armed | triggered | invalidated | resolved
    created_ctx: str = ""                  # context timestamp when first admitted
    why: str = ""
    execution: dict | None = None          # filled by M15/M1 monitoring (entry/stop/target/candidate/reason)

    def to_dict(self) -> dict:
        def px(x):
            return None if x is None else round(float(x), 2)
        return {"id": self.scenario_id, "direction": self.direction, "rank": self.rank,
                "state": self.state, "why": self.why, "created": self.created_ctx,
                "entry_zone": [px(self.entry_zone[0]), px(self.entry_zone[1])] if self.entry_zone else None,
                "draw": self.draw.to_dict(), "rank_factors": dict(self.rank_factors),
                "basis": dict(self.basis), "execution": self.execution}


def _range_key(dr) -> str:
    return "" if dr is None else f"{round(dr.low, 1)}-{round(dr.high, 1)}"


def _entry_zone(dr, direction: str):
    """The retracement region an entry would form in: discount (low→CE) for a long, premium (CE→high)
    for a short. None if there is no range."""
    if dr is None:
        return None
    return (dr.low, dr.ce) if direction == "long" else (dr.ce, dr.high)


def _next_seek_class(price, dr) -> str:
    """Lesson 10 alternation as a ranking hint: price beyond the range extreme just took ERL → it now
    seeks IRL; otherwise the terminal draw is ERL. Best-effort; only used to RANK, never to gate."""
    if dr is None or price is None:
        return "ERL"
    return "IRL" if (price > dr.high or price < dr.low) else "ERL"


def build_scenarios(strategic, intraday, objectives, *, price=None) -> list[Scenario]:
    """Turn the current draw-role liquidity objectives into ranked scenario proposals. Direction is set
    by the draw's side (a buy-side/high draw → long; sell-side/low → short). Ranked lexicographically by
    transparent factors: context alignment → Lesson-10 class fit → objective strength → proximity."""
    dr = getattr(strategic, "dealing_range", None) or getattr(intraday, "dealing_range", None)
    intraday_dir = getattr(intraday, "bias", "") or ""
    strat_bias = getattr(strategic, "bias", "") or ""
    next_class = _next_seek_class(price, dr)
    span = (dr.high - dr.low) if dr is not None else 0.0
    rkey = _range_key(dr)

    proposals: list[Scenario] = []
    for d in LQ.viable_targets(objectives):
        direction = "long" if d.side == "high" else "short" if d.side == "low" else None
        if direction is None:
            continue
        align = (2 if direction == intraday_dir else 1 if direction == strat_bias else 0)
        class_fit = 1 if d.liquidity_class == next_class else 0
        proximity = 0.0
        if price is not None and span > 0:
            proximity = round(max(0.0, 1.0 - abs(d.price - price) / span), 3)
        factors = {"alignment": align, "class_fit": class_fit,
                   "strength": round(d.strength, 3), "proximity": proximity}
        why = (f"{direction} toward {d.label} {round(d.price, 2)} ({d.liquidity_class or '—'}, {d.tf}); "
               f"{'aligned with intraday' if align == 2 else 'aligned with HTF bias' if align == 1 else 'counter-context'}"
               f"{' · Lesson-10 next-seek class' if class_fit else ''}")
        sid = f"{direction}:{d.kind}:{round(d.price, 2)}:{rkey}"
        proposals.append(Scenario(
            scenario_id=sid, direction=direction, draw=d, entry_zone=_entry_zone(dr, direction),
            basis={"bias": strat_bias, "intraday_direction": intraday_dir, "range_key": rkey, "why": why},
            rank_factors=factors, why=why))

    # DEDUP by structural identity: the SAME level often appears on both 4H and 1H (a draw at that
    # price). Keep ONE proposal per scenario_id — the strongest (higher-TF) — so the id→proposal map is
    # unambiguous and the active set stays stable (otherwise the weaker duplicate could reshuffle it).
    best: dict[str, Scenario] = {}
    for s in proposals:
        cur = best.get(s.scenario_id)
        if cur is None or s.draw.strength > cur.draw.strength:
            best[s.scenario_id] = s
    proposals = list(best.values())
    proposals.sort(key=lambda s: (s.rank_factors["alignment"], s.rank_factors["class_fit"],
                                  s.rank_factors["strength"], s.rank_factors["proximity"]), reverse=True)
    for i, s in enumerate(proposals):
        s.rank = i
    return proposals


class ScenarioBook:
    """The stable active set. Rebuilt only when the engine calls `observe` on a CONTEXT close. Membership
    changes only on structural events; an asymmetric hold band (admit ≤ target, drop only past maxn)
    prevents churn. Between context closes the engine calls `monitor`, which updates each active
    scenario's execution STATE (watching→retracing→armed→triggered) WITHOUT touching membership."""

    def __init__(self, target: int = 2, maxn: int = 3):
        self.target = target
        self.maxn = maxn
        self.active: list[Scenario] = []
        self.retired: list[Scenario] = []        # invalidated/resolved (audit; capped)

    def _retire(self, s: Scenario, state: str):
        s.state = state
        self.retired.append(s)
        self.retired = self.retired[-20:]

    def _still_valid(self, s: Scenario, fresh, cur_range_key: str) -> bool:
        """A scenario is invalidated ONLY by a structural event: its draw disappeared or was taken, the
        dealing range was superseded, or the fresh draw is spent. (Bias-flip is captured indirectly:
        a confirmed opposite shift changes the range/draw set. Kept conservative — never invalidate on
        price noise.)"""
        p = fresh.get(s.scenario_id)
        if p is None:                                        # the draw no longer exists in context
            return False
        if s.basis.get("range_key") != cur_range_key:        # dealing range superseded
            return False
        if p.draw.status in ("swept", "mitigated"):           # the target got taken → path resolved
            return False
        return True

    def observe(self, proposals: list[Scenario], *, context_key: str, cur_range_key: str) -> list[Scenario]:
        """Reconcile the active set with fresh proposals from the current context (call on a context
        close). Persist survivors in place, admit newcomers into the target band, drop invalidated ones
        and anything past the hold band."""
        fresh = {p.scenario_id: p for p in proposals}
        survivors: list[Scenario] = []
        for s in self.active:
            if not self._still_valid(s, fresh, cur_range_key):
                self._retire(s, "resolved" if fresh.get(s.scenario_id) and
                             fresh[s.scenario_id].draw.status in ("swept", "mitigated") else "invalidated")
                continue
            p = fresh[s.scenario_id]                          # persist IN PLACE, refresh volatile fields
            s.rank, s.draw, s.entry_zone = p.rank, p.draw, p.entry_zone
            s.rank_factors, s.why, s.basis = p.rank_factors, p.why, p.basis
            survivors.append(s)
        survivor_ids = {s.scenario_id for s in survivors}
        newcomers = [p for p in proposals if p.scenario_id not in survivor_ids and p.rank < self.target
                     and p.draw.status not in ("swept", "mitigated")]   # never admit a spent draw
        for p in newcomers:
            p.created_ctx = context_key
        combined = survivors + newcomers
        combined.sort(key=lambda s: s.rank)                   # by fresh plausibility rank
        self.active = combined[: self.maxn]                   # hold band: keep ≤ maxn (survivors sticky)
        return self.active

    def monitor(self, execute_fn) -> list[Scenario]:
        """Between context closes: update each active scenario's execution state via `execute_fn(s)`
        (which returns an execution dict or None). Never changes membership — pure state refresh, so
        M1/M5 ticks can advance watching→retracing→armed→triggered without churning the set."""
        for s in self.active:
            ex = execute_fn(s)
            s.execution = ex
            if ex is None:
                if s.state not in ("watching", "retracing"):
                    s.state = "watching"
            else:
                s.state = ex.get("state", s.state)
        return self.active

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.active]
