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
    draw_ladder: list = field(default_factory=list)   # farther same-direction draws above/below (extensions)
    state: str = "watching"                # watching | retracing | armed | triggered | target | stop | invalidated
    created_ctx: str = ""                  # context timestamp when first admitted
    why: str = ""
    execution: dict | None = None          # filled by M15/M1 monitoring (entry/stop/target/candidate/reason)
    position: dict | None = None           # OPEN TRADE snapshot once triggered — fixed entry/stop/target;
    #                                        resolves ONLY on stop/target (a trigger is sticky, not re-derived)

    def to_dict(self) -> dict:
        def px(x):
            return None if x is None else round(float(x), 2)
        return {"id": self.scenario_id, "direction": self.direction, "rank": self.rank,
                "state": self.state, "why": self.why, "created": self.created_ctx,
                "entry_zone": [px(self.entry_zone[0]), px(self.entry_zone[1])] if self.entry_zone else None,
                "draw": self.draw.to_dict(), "rank_factors": dict(self.rank_factors),
                "draw_ladder": [{"label": o.label, "price": px(o.price), "tf": o.tf,
                                 "liquidity_class": o.liquidity_class} for o in self.draw_ladder],
                "basis": dict(self.basis), "execution": self.execution, "position": self.position}


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
        dist = abs(d.price - price) if price is not None else 1e9      # raw distance (nearest-first sort)
        proximity = round(max(0.0, 1.0 - dist / span), 3) if (price is not None and span > 0) else 0.0
        factors = {"alignment": align, "class_fit": class_fit,
                   "strength": round(d.strength, 3), "proximity": proximity, "_dist": dist}
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
    # Lesson 10: price seeks the NEXT (nearest) draw. Rank by direction (alignment) → ERL/IRL class fit →
    # then the CLOSEST objective (raw distance, so far draws don't all tie at proximity 0) → strength.
    proposals.sort(key=lambda s: (-s.rank_factors["alignment"], -s.rank_factors["class_fit"],
                                  s.rank_factors["_dist"], -s.rank_factors["strength"]))
    # COLLAPSE same-direction / same-retracement-zone theses into ONE trade. They share the entry FVG and
    # stop, and the target is the nearest liquidity past 2R regardless of which far draw anchored the
    # thesis — so 3 "long toward BSL x/y/z" are ONE trade. Keep the nearest (best-ranked); the farther
    # same-direction draws become that scenario's draw_ladder (extension targets above/below).
    merged: dict[tuple, Scenario] = {}
    for s in proposals:
        z = (round(s.entry_zone[0], 1), round(s.entry_zone[1], 1)) if s.entry_zone else None
        key = (s.direction, z)
        keep = merged.get(key)
        if keep is None:
            merged[key] = s
        else:
            keep.draw_ladder.append(s.draw)           # a farther same-direction draw → extension target
    proposals = list(merged.values())
    for i, s in enumerate(proposals):
        s.rank = i
    return proposals


class ScenarioBook:
    """The stable active set. Rebuilt only when the engine calls `observe` on a CONTEXT close. Membership
    changes only on structural events; an asymmetric hold band (admit ≤ target, drop only past maxn)
    prevents churn. Between context closes the engine calls `monitor`, which updates each active
    scenario's execution STATE (watching→retracing→armed→triggered) WITHOUT touching membership."""

    def __init__(self, target: int = 2, maxn: int = 3, on_event=None, point_value=None):
        self.target = target
        self.maxn = maxn
        self.point_value = point_value           # $ per point (1 contract) — for dollar P&L; None = R only
        self.active: list[Scenario] = []
        self.retired: list[Scenario] = []        # invalidated/closed (audit; capped)
        self.trades: list[dict] = []             # ONE record per triggered position (open + its outcome)
        self.on_event = on_event                 # optional callback(record) on OPEN and on CLOSE
        self._last_bar = None                    # last execution bar seen (EOD exit price)
        self._last_day = None                    # last CME session day seen (roll-over detection)

    def _retire(self, s: Scenario, state: str):
        s.state = state
        self.retired.append(s)
        self.retired = self.retired[-40:]

    # ---- open-trade lifecycle: a TRIGGER is sticky and resolves ONLY on stop/target -------------
    @staticmethod
    def _is_open(s: Scenario) -> bool:
        return bool(s.position and s.position.get("open"))

    @staticmethod
    def _rmult(entry, stop, target):
        risk = abs(entry - stop) if (entry is not None and stop is not None) else 0
        return round(abs(target - entry) / risk, 2) if (risk > 0 and target is not None) else None

    def _pnl(self, entry, exit_px, direction):
        """Dollar P&L of 1 contract from entry to exit_px (+ profit / − loss). None if no point value."""
        if self.point_value is None or entry is None or exit_px is None:
            return None
        move = (exit_px - entry) if direction == "long" else (entry - exit_px)
        return round(move * self.point_value, 2)

    def _open_position(self, s: Scenario, ex: dict, bar) -> None:
        e, stp, tgt = ex.get("entry"), ex.get("stop"), ex.get("target")
        rec = {"scenario_id": s.scenario_id, "direction": s.direction, "draw": s.draw.label,
               "draw_price": round(s.draw.price, 2), "entry": e, "stop": stp, "target": tgt,
               "rmult": self._rmult(e, stp, tgt), "open": True,
               "opened_at": (bar.close_time.isoformat() if bar is not None else None),
               "opened_price": (round(bar.close, 2) if bar is not None else None),
               "order": ex.get("order"), "sl_order": ex.get("sl_order"), "tp_order": ex.get("tp_order"),
               "fvg_top": ex.get("fvg_top"), "fvg_bottom": ex.get("fvg_bottom"),
               "now": (round(bar.close, 2) if bar is not None else e),
               "pnl_usd": (self._pnl(e, bar.close, s.direction) if bar is not None else 0.0),  # CURRENT $ (live/realized)
               "plan_usd": self._pnl(e, tgt, s.direction),        # ORIGINAL $ — planned profit if the target is hit
               "risk_usd": self._pnl(e, stp, s.direction),        # $ at the stop (the amount risked)
               "outcome": None, "result_r": None, "closed_at": None}
        s.position = rec                         # scenario shares the trade record
        s.state = "triggered"
        self.trades.append(rec)
        if self.on_event:
            try: self.on_event(dict(rec))
            except Exception: pass

    def _resolve_position(self, s: Scenario, outcome: str, bar) -> None:
        p = s.position
        p["open"] = False
        p["outcome"] = outcome                   # "target" | "stop"
        p["result_r"] = (p["rmult"] if (outcome == "target" and p["rmult"] is not None) else
                         (1.0 if outcome == "target" else -1.0))
        exit_px = p["target"] if outcome == "target" else p["stop"]
        p["pnl_usd"] = self._pnl(p["entry"], exit_px, p["direction"])   # realized $ at target/stop
        p["closed_at"] = (bar.close_time.isoformat() if bar is not None else None)
        s.state = outcome
        if self.on_event:
            try: self.on_event(dict(p))
            except Exception: pass

    def _resolve_eod(self, s: Scenario, exit_bar) -> None:
        """Close an OPEN trade at the session's last price — NO overnight holds (intraday day-trading;
        there is no trade between days). Realized R is the signed move from entry to the exit price."""
        p = s.position
        exit_px = float(exit_bar.close) if exit_bar is not None else p["entry"]
        risk = abs(p["entry"] - p["stop"]) if (p["entry"] is not None and p["stop"] is not None) else 0
        move = (exit_px - p["entry"]) if p["direction"] == "long" else (p["entry"] - exit_px)
        p["open"] = False
        p["outcome"] = "eod"
        p["result_r"] = round(move / risk, 2) if risk > 0 else 0.0
        p["exit_price"] = round(exit_px, 2)
        p["pnl_usd"] = self._pnl(p["entry"], exit_px, p["direction"])   # realized $ at session-end exit
        p["closed_at"] = (exit_bar.close_time.isoformat() if exit_bar is not None else None)
        s.state = "eod"
        if self.on_event:
            try: self.on_event(dict(p))
            except Exception: pass

    def _update_open(self, s: Scenario, bar) -> None:
        """An OPEN trade resolves ONLY when the bar's range touches stop or target (no-look-ahead: if a
        bar hits both, the stop wins — the pessimistic assumption). Until then it stays 'triggered'."""
        if bar is None:
            return
        p = s.position
        stp, tgt, d = p["stop"], p["target"], p["direction"]
        hi, lo = float(bar.high), float(bar.low)
        if d == "long":
            if stp is not None and lo <= stp:
                self._resolve_position(s, "stop", bar)
            elif tgt is not None and hi >= tgt:
                self._resolve_position(s, "target", bar)
        else:  # short
            if stp is not None and hi >= stp:
                self._resolve_position(s, "stop", bar)
            elif tgt is not None and lo <= tgt:
                self._resolve_position(s, "target", bar)
        if p.get("open"):                                # still running → update the live $ since start
            p["now"] = round(float(bar.close), 2)
            p["pnl_usd"] = self._pnl(p["entry"], float(bar.close), d)

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
        # OPEN trades are never churned — they stay until stop/target resolves them (a trigger is sticky).
        # A just-resolved trade (target/stop) is dropped here on the next context close, after it showed.
        open_trades = [s for s in self.active if self._is_open(s)]
        theses = [s for s in self.active if not self._is_open(s) and s.state not in ("target", "stop", "eod")]
        survivors: list[Scenario] = []
        for s in theses:
            if not self._still_valid(s, fresh, cur_range_key):
                self._retire(s, "invalidated")
                continue
            p = fresh[s.scenario_id]                          # persist IN PLACE, refresh volatile fields
            s.rank, s.draw, s.entry_zone = p.rank, p.draw, p.entry_zone
            s.rank_factors, s.why, s.basis = p.rank_factors, p.why, p.basis
            survivors.append(s)
        held_ids = {s.scenario_id for s in survivors} | {s.scenario_id for s in open_trades}
        newcomers = [p for p in proposals if p.scenario_id not in held_ids and p.rank < self.target
                     and p.draw.status not in ("swept", "mitigated")]   # never admit a spent draw
        for p in newcomers:
            p.created_ctx = context_key
        combined = survivors + newcomers
        combined.sort(key=lambda s: s.rank)                   # by fresh plausibility rank
        self.active = open_trades + combined[: self.maxn]     # open trades kept + ≤ maxn theses
        return self.active

    def monitor(self, execute_fn, bar=None, day=None) -> list[Scenario]:
        """Called on each execution close. An OPEN trade is updated against `bar` (resolves only on
        stop/target) — it is NEVER re-derived, so a trigger cannot fall back to 'watching'. A thesis that
        is not yet open has its state derived from `execute_fn`; the moment it reads 'triggered' it is
        OPENED into a sticky position. `day` = the CME session day of `bar`; when it rolls over, any trade
        still open is closed at the prior session's last price (NO overnight holds). Membership untouched."""
        if (day is not None and self._last_day is not None and day != self._last_day
                and self._last_bar is not None):
            for s in self.active:                             # session rolled over → close all open trades
                if self._is_open(s):
                    self._resolve_eod(s, self._last_bar)
        for s in list(self.active):
            if self._is_open(s):
                self._update_open(s, bar)                     # may resolve → state target/stop
                continue
            if s.state in ("target", "stop", "eod"):          # already resolved — leave it until observe
                continue
            ex = execute_fn(s)
            s.execution = ex
            if ex and ex.get("state") == "triggered":
                self._open_position(s, ex, bar)               # sticky from here on
            elif ex is None:
                if s.state not in ("watching", "retracing"):
                    s.state = "watching"
            else:
                s.state = ex.get("state", s.state)
        if bar is not None:
            self._last_bar = bar
        if day is not None:
            self._last_day = day
        return self.active

    def stats(self) -> dict:
        """Trigger→outcome statistics for the session: how many triggered, and the win% of those that
        resolved. target = win, stop = loss, eod = closed at session end (no overnight holds); a win is
        any close with result_r > 0."""
        closed = [t for t in self.trades if not t["open"]]
        open_t = [t for t in self.trades if t["open"]]
        n = len(closed)
        wins = [t for t in closed if (t.get("result_r") or 0) > 0]
        cnt = lambda o: sum(1 for t in closed if t["outcome"] == o)
        has_usd = self.point_value is not None
        return {"triggered": len(self.trades), "open": len(open_t), "resolved": n,
                "target": cnt("target"), "stop": cnt("stop"), "eod": cnt("eod"),
                "wins": len(wins), "losses": n - len(wins),
                "win_pct": (round(100 * len(wins) / n, 1) if n else None),
                "total_r": round(sum((t.get("result_r") or 0.0) for t in closed), 2),
                "total_usd": (round(sum((t.get("pnl_usd") or 0.0) for t in closed), 2) if has_usd else None),
                "open_usd": (round(sum((t.get("pnl_usd") or 0.0) for t in open_t), 2) if has_usd else None)}

    def to_list(self) -> list[dict]:
        return [s.to_dict() for s in self.active]
