"""Persistent Lesson-15 POTENTIAL state (V2_GAP lifecycle fix).

The runtime evidence (ict_v2/docs/V2_GAP.md) showed that re-deriving the 5m structure every close is NOT
behaviorally equivalent to carrying the Lesson-15 state: a valid POTENTIAL flickers out of the re-built
skeleton within ~1 close, so the sequence never runs to its course-defined terminal event on the same tracked
event. This module carries the POTENTIAL as first-class structural state.

  * CREATION is still validated ONLY by the existing 5m sequence logic (`pipeline._trend_sequence`); this
    module never re-defines what a valid Lesson-15 sequence is.
  * IDENTITY is frozen at creation (direction + prior-trend structure + S[k] + S[k-1] + failed-continuation
    pivot). `created_at` is metadata, NOT part of identity — the same structural thesis rediscovered on a
    later close is absorbed into the existing state, never duplicated.
  * TRANSITIONS run only on subsequent CLOSED 5m bars, against the FROZEN references (never re-anchored):
      CANCELLED  — a new structural swing beyond the frozen S[k-1] (prior trend resumed): long ⇒ a structural
                   low < S[k-1]; short ⇒ a structural high > S[k-1] (pivot after the failed-continuation).
      CONFIRMED  — a valid confirming displacement body-closes beyond the frozen S[k] price (existing
                   displacement/confirmation rule, evaluated against the immutable frozen target).
      else       — remain POTENTIAL.
  * Terminal states never resurrect. Active POTENTIALs are NEVER evicted by a cap (a very high sanity limit
    only logs); terminal history is capped for memory/UI only.
  * The lifecycle is independent of H4/H1 scenarios, P/D, WHERE, target, >=2R and entry — those consume a
    CONFIRMED reversal downstream; they never decide whether the structural event exists.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("ict_v2.reversals")

_SANITY_ACTIVE = 500          # loud-guard only; NEVER evicts a valid POTENTIAL (see decision #1)
_TERMINAL_CAP = 2000          # history cap for memory/UI only (terminal records, not active state)


@dataclass
class _Swing:
    price: float
    pivot_time: Optional[str]      # absolute open_time of the pivot bar (ISO)
    knowable_time: Optional[str]   # absolute close_time of the bar at which it became knowable (ISO)
    kind: str = ""


@dataclass
class Potential:
    # ---- frozen identity (never mutated) ----
    direction: str                 # "long" | "short"
    prior_trend: str               # "up (HH/HL)" | "down (LH/LL)"
    fcp: _Swing                    # failed-continuation pivot (LH after up / HL after down)
    s_k_minus_1: _Swing            # prior-trend extreme — CANCELLATION reference
    s_k: _Swing                    # opposing structural swing to break — CONFIRMATION target
    created_at: str                # first close this identity became knowable (metadata, NOT identity)
    knowable_at: str               # max knowable_time of the frozen pivots (causal stamp)
    # ---- mutable lifecycle ----
    state: str = "potential"       # potential -> confirmed | cancelled  (terminal)
    confirmed_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    resumed_extreme: Optional[float] = None
    create_chain: dict = field(default_factory=dict)   # WHERE/leg of the CREATING sweep→disp (watching audit)
    confirm_chain: dict = field(default_factory=dict)   # WHERE/leg of the CONFIRMING displacement (entry)
    emitted: bool = False          # a downstream scenario has consumed this CONFIRMED event (consume-once)
    rediscoveries: int = 0         # times the same frozen identity was re-detected on later closes

    @property
    def identity(self):
        return (self.direction, self.s_k.price, self.s_k_minus_1.price, self.fcp.price)


def _round_tick(x, tick):
    return round(round(float(x) / tick) * tick, 6)


class ReversalBook:
    """Owns the persistent Lesson-15 POTENTIAL lifecycle for one engine. Updated exactly once per 5m close."""

    def __init__(self, price_dp: int = 2, tick: float = 0.25):
        self.price_dp, self.tick = price_dp, tick
        self.active: list[Potential] = []      # POTENTIAL (still evolving)
        self.confirmed: list[Potential] = []   # CONFIRMED, available to downstream (until consumed)
        self.terminal: list[Potential] = []    # CANCELLED + consumed CONFIRMED (history, capped)
        self._seen: dict = {}                  # identity -> Potential (all-time; prevents resurrection & dedups)
        self.n_created = self.n_confirmed = self.n_cancelled = self.n_rediscovered = 0
        self._peak_active = {"long": 0, "short": 0}

    # ---- the once-per-5m-close update ------------------------------------------------------------
    def update(self, confirm_ms, bars5, cursor: str) -> None:
        if confirm_ms is None or not bars5:
            return
        self._advance(confirm_ms, bars5, cursor)     # advance existing FIRST (terminal-once)
        self._create(confirm_ms, bars5, cursor)      # then admit newly-valid sequences
        for d in ("long", "short"):
            self._peak_active[d] = max(self._peak_active[d], sum(1 for p in self.active if p.direction == d))
        if len(self.active) > _SANITY_ACTIVE:        # loud guard ONLY — never evict a valid potential
            log.warning("ReversalBook active POTENTIALs=%d exceeds sanity limit %d (not evicting)",
                        len(self.active), _SANITY_ACTIVE)

    # ---- creation: sole validator is pipeline._trend_sequence -----------------------------------
    def _create(self, confirm_ms, bars5, cursor: str) -> None:
        from ict_v2 import pipeline as P
        S = list(getattr(confirm_ms, "structural", []) or [])
        if not S:
            return
        idx_of = {getattr(s, "index", None): j for j, s in enumerate(S)}
        for r in getattr(confirm_ms, "ranked_mss", []):
            m = r.item
            direction = "short" if getattr(m, "direction", "") == "bearish" else "long"
            kind, det = P._trend_sequence(S, m, direction)
            if kind != "reversal":                    # only a valid Lesson-15 sequence creates a POTENTIAL
                continue
            k = idx_of.get(getattr(m, "broken_index", None))
            if k is None or k < 3 or k + 1 >= len(S):
                continue
            sk = self._freeze(S[k], bars5)
            skm1 = self._freeze(S[k - 1], bars5)
            fcp = self._freeze(S[k + 1], bars5)
            key = (direction, sk.price, skm1.price, fcp.price)
            if key in self._seen:                     # same frozen identity → absorb the rediscovery (no dup, no resurrect)
                self._seen[key].rediscoveries += 1
                self.n_rediscovered += 1
                continue
            p = Potential(direction=direction, prior_trend=det.get("prior_trend", ""), fcp=fcp,
                          s_k_minus_1=skm1, s_k=sk, created_at=cursor,
                          knowable_at=max(x for x in (sk.knowable_time, skm1.knowable_time, fcp.knowable_time) if x)
                          if any((sk.knowable_time, skm1.knowable_time, fcp.knowable_time)) else cursor,
                          create_chain=self._chain(confirm_ms, m))
            self._seen[key] = p
            self.active.append(p)
            self.n_created += 1
            if getattr(m, "state", "") == "confirmed":   # born already confirmed → confirm now with its own chain
                self._confirm(p, cursor, self._chain(confirm_ms, m))

    # ---- advance existing potentials against FROZEN references -----------------------------------
    def _advance(self, confirm_ms, bars5, cursor: str) -> None:
        S = list(getattr(confirm_ms, "structural", []) or [])
        last = bars5[-1]
        disps = [r.item for r in getattr(confirm_ms, "ranked_displacements", [])]
        for p in list(self.active):
            # CANCEL — a NEW structural swing beyond the frozen S[k-1], pivot after the failed-continuation
            resumed = self._resumption(S, p)
            if resumed is not None:
                p.state, p.cancelled_at, p.resumed_extreme = "cancelled", cursor, resumed
                self._retire(p)
                self.n_cancelled += 1
                continue
            # CONFIRM — a valid confirming displacement body-closes beyond the frozen S[k] price
            chain = self._confirming_chain(confirm_ms, disps, p, last)
            if chain is not None:
                self._confirm(p, cursor, chain)

    def _resumption(self, S, p: Potential):
        """The prior trend made a new structural extreme beyond the FROZEN S[k-1] after the failed-cont pivot."""
        ft = p.fcp.pivot_time                         # isoformat string frozen at creation
        for s in S:
            st = getattr(s, "time", None)
            st = st.isoformat() if st is not None else None
            if st is not None and ft is not None and st <= ft:
                continue                              # only swings that formed AFTER the failed-continuation pivot
            if p.direction == "short" and getattr(s, "kind", "") == "high" and float(s.price) > p.s_k_minus_1.price:
                return _round_tick(s.price, self.tick)
            if p.direction == "long" and getattr(s, "kind", "") == "low" and float(s.price) < p.s_k_minus_1.price:
                return _round_tick(s.price, self.tick)
        return None

    def _confirming_chain(self, confirm_ms, disps, p: Potential, last):
        """Confirmation = the latest closed 5m bar body-closes beyond the FROZEN S[k] price (the break) AND a
        reversal-direction displacement is present (existing displacement/confirmation rule certifies the
        break is energetic). The body-close is the break; the displacement supplies the entry chain. None if
        either is absent. Never re-anchors S[k]."""
        broke = (last.close < p.s_k.price) if p.direction == "short" else (last.close > p.s_k.price)
        if not broke:
            return None
        want = "bearish" if p.direction == "short" else "bullish"
        cands = [d for d in disps if getattr(d, "direction", "") == want]
        if not cands:
            return None
        cand = max(cands, key=lambda x: getattr(x, "end_index", getattr(x, "start_index", 0)))
        return self._chain(confirm_ms, cand, is_disp=True)

    # ---- helpers --------------------------------------------------------------------------------
    def _freeze(self, s, bars5) -> _Swing:
        ci = getattr(s, "confirm_index", None)
        kn = bars5[ci].close_time.isoformat() if (ci is not None and 0 <= ci < len(bars5)) else None
        pt = getattr(s, "time", None)
        return _Swing(price=_round_tick(s.price, self.tick),
                      pivot_time=(pt.isoformat() if pt is not None else None),
                      knowable_time=kn, kind=getattr(s, "kind", ""))

    def _chain(self, confirm_ms, item, is_disp: bool = False) -> dict:
        """Extract WHERE (sweep manip) + leg + same-leg FVGs from a creating MSS or a confirming displacement,
        reusing the v1 depends_on links exactly as pipeline._structural_reversal does."""
        disp_by = {d.item.id: d.item for d in getattr(confirm_ms, "ranked_displacements", [])}
        sweep_by = {s.item.id: s.item for s in getattr(confirm_ms, "ranked_sweeps", [])}
        d = item if is_disp else (disp_by.get(item.depends_on[0]) if getattr(item, "depends_on", None) else None)
        if d is None:
            return {}
        sw = sweep_by.get(d.depends_on[0]) if getattr(d, "depends_on", None) else None
        manip = getattr(sw, "extreme", None)
        if manip is None:
            manip = getattr(d, "start_price", None)
        did = getattr(d, "id", None)
        fvgs = [r.item for r in getattr(confirm_ms, "ranked_fvgs", [])
                if getattr(r.item, "depends_on", None) and r.item.depends_on[0] == did
                and getattr(r.item, "status", "") != "mitigated"]
        return {"manip": (float(manip) if manip is not None else None),
                "leg_a": float(getattr(d, "start_price", manip if manip is not None else 0.0)),
                "leg_b": float(getattr(d, "end_price", manip if manip is not None else 0.0)),
                "pool": float(getattr(sw, "pool_price", manip)) if sw is not None else (float(manip) if manip is not None else None),
                "fvgs": fvgs}

    def _confirm(self, p: Potential, cursor: str, chain: dict) -> None:
        p.state, p.confirmed_at, p.confirm_chain = "confirmed", cursor, (chain or p.create_chain)
        if p in self.active:
            self.active.remove(p)
        self.confirmed.append(p)
        self.n_confirmed += 1

    def _retire(self, p: Potential) -> None:
        if p in self.active:
            self.active.remove(p)
        self.terminal.append(p)
        if len(self.terminal) > _TERMINAL_CAP:
            del self.terminal[:-_TERMINAL_CAP]

    # ---- downstream query (execution layer) -----------------------------------------------------
    def for_scenario(self, direction: str, lo: float, hi: float) -> Optional[dict]:
        """Return the current structural reversal for a scenario of `direction` whose manipulation is inside
        the [lo,hi] P/D half — a CONFIRMED one (preferred) else a forming POTENTIAL — in the dict shape the
        execution layer already consumes. None if nothing structural applies. Never re-anchors."""
        def in_half(ch):
            m = (ch or {}).get("manip")
            return m is not None and lo <= float(m) <= hi

        cs = [p for p in self.confirmed if p.direction == direction and not p.emitted and in_half(p.confirm_chain)]
        if cs:
            p = max(cs, key=lambda x: x.confirmed_at or "")
            return self._as_dict(p, "confirmed", p.confirm_chain)
        ps = [p for p in self.active if p.direction == direction and in_half(p.create_chain)]
        if ps:
            p = max(ps, key=lambda x: x.created_at or "")
            return self._as_dict(p, "potential", p.create_chain)
        return None

    def _as_dict(self, p: Potential, state: str, chain: dict) -> dict:
        seq = {"prior_trend": p.prior_trend,
               "failed_continuation_pivot": (f"{p.fcp.kind[0].upper()}{'H' if p.fcp.kind=='high' else 'L'} {p.fcp.price}"
                                             if p.fcp.kind else None),
               "prior_opposing_extreme": p.s_k_minus_1.price, "last_structural_swing": p.s_k.price,
               "break_kind": ("HH" if p.direction == "long" else "LL")}
        manip = (chain or {}).get("manip")
        return {"state": state, "manip": manip,
                "leg_a": (chain or {}).get("leg_a", manip), "leg_b": (chain or {}).get("leg_b", manip),
                "broken_price": p.s_k.price, "broken_dominant": False, "broken_protected": False,
                "seq": seq, "classification": "reversal", "pool": (chain or {}).get("pool", manip),
                "mss_id": p.identity[0] + ":" + str(p.s_k.price), "fvgs": (chain or {}).get("fvgs", [])}

    def mark_emitted(self, mss_id: str) -> None:
        for p in self.confirmed:
            if self._as_dict(p, "confirmed", p.confirm_chain)["mss_id"] == mss_id and not p.emitted:
                p.emitted = True
                self._retire_confirmed(p)
                return

    def _retire_confirmed(self, p: Potential) -> None:
        if p in self.confirmed:
            self.confirmed.remove(p)
        self.terminal.append(p)

    def census(self) -> dict:
        return {"created": self.n_created, "confirmed": self.n_confirmed, "cancelled": self.n_cancelled,
                "active_now": len(self.active), "confirmed_now": len(self.confirmed),
                "rediscoveries_absorbed": self.n_rediscovered,
                "peak_simultaneous_active": dict(self._peak_active)}
