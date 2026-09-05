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
    locality_reject: Optional[dict] = None   # latest reason a break did NOT confirm for lack of a local breaking leg
    quality_reject: Optional[dict] = None    # latest reason a LOCAL breaking leg lacked relative candle expansion

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
        piv = self._minor_pivots(bars5)              # width-1 minor pivots (v1's own leg segmentation)
        self._advance(confirm_ms, bars5, cursor, piv)  # advance existing FIRST (terminal-once)
        self._create(confirm_ms, bars5, cursor, piv)   # then admit newly-valid sequences
        for d in ("long", "short"):
            self._peak_active[d] = max(self._peak_active[d], sum(1 for p in self.active if p.direction == d))
        if len(self.active) > _SANITY_ACTIVE:        # loud guard ONLY — never evict a valid potential
            log.warning("ReversalBook active POTENTIALs=%d exceeds sanity limit %d (not evicting)",
                        len(self.active), _SANITY_ACTIVE)

    # ---- creation: sole validator is pipeline._trend_sequence -----------------------------------
    def _create(self, confirm_ms, bars5, cursor: str, piv) -> None:
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
            if getattr(m, "state", "") == "confirmed":   # born already confirmed → v1-linked chain, if it qualifies
                dd = self._disp_of(confirm_ms, m)        # the v1 MSS's OWN displacement (local by construction)
                ok, q = self._body_quality(dd, bars5, piv) if dd is not None else (False, None)
                if ok:                                    # relative candle expansion present → confirm
                    chain = self._chain(confirm_ms, m)
                    chain["locality"] = self._disp_audit(dd, bars5, len(bars5) - 1, p, self._spans(dd, p), True, cursor)
                    chain["locality"]["source"] = "born-confirmed (v1 MSS-linked displacement)"
                    chain["quality"] = q
                    self._confirm(p, cursor, chain)
                else:                                     # no expansion → stays POTENTIAL (re-checked each close)
                    p.quality_reject = q or {"reason": "born-confirmed leg had no displacement to grade"}

    # ---- advance existing potentials against FROZEN references -----------------------------------
    def _advance(self, confirm_ms, bars5, cursor: str, piv) -> None:
        S = list(getattr(confirm_ms, "structural", []) or [])
        disps = [r.item for r in getattr(confirm_ms, "ranked_displacements", [])]
        for p in list(self.active):
            # CANCEL — a NEW structural swing beyond the frozen S[k-1], pivot after the failed-continuation
            resumed = self._resumption(S, p)
            if resumed is not None:
                p.state, p.cancelled_at, p.resumed_extreme = "cancelled", cursor, resumed
                self._retire(p)
                self.n_cancelled += 1
                continue
            # CONFIRM — the LOCAL displacement that breaks frozen S[k] AND shows relative candle expansion
            chain = self._confirming_chain(confirm_ms, disps, p, bars5, piv)
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

    def _spans(self, d, p: Potential) -> bool:
        """The displacement leg crosses the FROZEN S[k]: it starts on the origin side and ends BEYOND S[k]
        (short: start >= S[k] > end; long: start <= S[k] < end). A leg that ends before reaching S[k] fails."""
        sp, ep = float(getattr(d, "start_price", 0.0)), float(getattr(d, "end_price", 0.0))
        return (sp >= p.s_k.price > ep) if p.direction == "short" else (sp <= p.s_k.price < ep)

    def _disp_audit(self, d, bars5, bi, p, spans, belongs, confirm_time) -> dict:
        si, ei = getattr(d, "start_index", None), getattr(d, "end_index", None)
        ot = lambda ix: bars5[ix].open_time.isoformat() if (ix is not None and 0 <= ix < len(bars5)) else None
        ct = lambda ix: bars5[ix].close_time.isoformat() if (ix is not None and 0 <= ix < len(bars5)) else None
        return {"disp_start_price": round(float(getattr(d, "start_price", 0.0)), 2),
                "disp_end_price": round(float(getattr(d, "end_price", 0.0)), 2),
                "disp_start_time": ot(si), "disp_end_time": ct(ei), "disp_start_index": si, "disp_end_index": ei,
                "s_k": p.s_k.price, "confirm_bar_time": confirm_time, "spans_s_k": spans, "confirm_bar_belongs": belongs}

    def _confirming_chain(self, confirm_ms, disps, p: Potential, bars5, piv):
        """LOCALITY + DISPLACEMENT-QUALITY confirmation (V2_GAP). The confirming displacement must be the leg
        that actually breaks the frozen S[k] (locality: reversal-direction, spanning S[k], confirmation bar
        within its span — a stale/earlier or short-of-S[k] leg does NOT qualify) AND it must show the course's
        relative candle expansion (Lesson 12: 'very large candles relative to the candles that preceded them',
        mechanized as body-max vs the preceding minor leg — see _body_quality). Deterministic/causal; no
        numeric threshold; never re-anchors S[k]. Returns the chain, or None + a recorded rejection reason."""
        last = bars5[-1]
        bi = len(bars5) - 1
        broke = (last.close < p.s_k.price) if p.direction == "short" else (last.close > p.s_k.price)
        if not broke:
            return None
        want = "bearish" if p.direction == "short" else "bullish"
        confirm_t = last.close_time.isoformat()
        local = [d for d in disps if getattr(d, "direction", "") == want and self._spans(d, p)
                 and getattr(d, "start_index", None) is not None and getattr(d, "end_index", None) is not None
                 and d.start_index <= bi <= d.end_index]
        if not local:
            spanning = [d for d in disps if getattr(d, "direction", "") == want and self._spans(d, p)]
            reason = ("break not produced by a LOCAL displacement — a reversal-direction displacement spans "
                      "S[k] but ended before the break bar (stale/earlier leg)" if spanning else
                      "no reversal-direction displacement reaches THROUGH S[k] (break is a drift, not a "
                      "displacement's breaking leg)")
            p.locality_reject = {"reason": reason, "s_k": p.s_k.price, "break_close": round(float(last.close), 2),
                                 "confirm_bar_time": confirm_t,
                                 "stale_spanning": [self._disp_audit(d, bars5, bi, p, True, False, confirm_t) for d in spanning[:3]]}
            return None
        p.locality_reject = None
        # DISPLACEMENT QUALITY — among the local breaking legs, require relative candle expansion (Lesson 12).
        qualified = []
        for d in sorted(local, key=lambda x: getattr(x, "end_index", 0), reverse=True):
            ok, q = self._body_quality(d, bars5, piv)
            if ok:
                chain = self._chain(confirm_ms, d, is_disp=True)
                chain["locality"] = self._disp_audit(d, bars5, bi, p, True, True, confirm_t)
                chain["quality"] = q
                p.quality_reject = None
                return chain
            qualified.append(q)
        p.quality_reject = {"reason": "local breaking leg has NO relative candle expansion (Lesson 12: body-max "
                            "not greater than the preceding minor leg's body-max)", **(qualified[0] or {})}
        return None

    # ---- helpers --------------------------------------------------------------------------------
    def _freeze(self, s, bars5) -> _Swing:
        ci = getattr(s, "confirm_index", None)
        kn = bars5[ci].close_time.isoformat() if (ci is not None and 0 <= ci < len(bars5)) else None
        pt = getattr(s, "time", None)
        return _Swing(price=_round_tick(s.price, self.tick),
                      pivot_time=(pt.isoformat() if pt is not None else None),
                      knowable_time=kn, kind=getattr(s, "kind", ""))

    def _disp_of(self, confirm_ms, m):
        disp_by = {d.item.id: d.item for d in getattr(confirm_ms, "ranked_displacements", [])}
        return disp_by.get(m.depends_on[0]) if getattr(m, "depends_on", None) else None

    @staticmethod
    def _minor_pivots(bars5) -> list:
        """Width-1 minor pivot indices — v1's OWN leg segmentation (SwingDetector(1)), so the 'preceding minor
        leg' boundary is defined exactly as the displacement detector defines legs (no arbitrary N-bar window)."""
        from ict_live.structure.swings import SwingDetector
        det = SwingDetector(1)
        for b in bars5:
            det.add(b)
        return sorted(s.index for s in det.confirmed())

    @staticmethod
    def _body(b) -> float:
        return abs(float(b.close) - float(b.open))

    def _body_quality(self, d, bars5, piv):
        """Lesson-12 relative candle EXPANSION [RES: body vs range → BODY; comparison set → the immediately-
        preceding minor leg]. Returns (ok, audit). Rule (ordinal, no threshold):
            max |close-open| over the confirming displacement leg  >  max |close-open| over the candles of the
            immediately-preceding minor leg (from the last width-1 minor pivot before the sweep, up to it)."""
        si = getattr(d, "start_index", None)
        ei = getattr(d, "end_index", None)
        if si is None or ei is None or not bars5:
            return (False, {"quality_basis": "body max vs preceding minor-leg body max [RES]", "reason": "no leg indices"})
        disp = bars5[si:ei + 1]
        disp_max = max((self._body(b) for b in disp), default=0.0)
        prev_start = max((i for i in piv if i < si), default=0)          # last minor pivot strictly before the sweep
        prec = bars5[prev_start:si]                                      # the immediately-preceding minor leg
        prec_max = max((self._body(b) for b in prec), default=0.0)
        ok = disp_max > prec_max                                         # strictly greater — the "elephant"
        ot = lambda ix: bars5[ix].open_time.isoformat() if (ix is not None and 0 <= ix < len(bars5)) else None
        return (ok, {"quality_basis": "body max vs preceding minor-leg body max [RES]",
                     "disp_max_body": round(disp_max, 2), "preceding_leg_max_body": round(prec_max, 2),
                     "preceding_leg_from": ot(prev_start), "preceding_leg_to": ot(max(si - 1, prev_start)),
                     "preceding_leg_from_index": prev_start, "preceding_leg_to_index": si - 1,
                     "expands": ok})

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
                "mss_id": p.identity[0] + ":" + str(p.s_k.price), "fvgs": (chain or {}).get("fvgs", []),
                "locality": (chain or {}).get("locality"),                 # confirmed: the breaking-leg audit
                "quality": (chain or {}).get("quality"),                   # confirmed: the candle-expansion audit
                "locality_reject": (p.locality_reject if state == "potential" else None),
                "quality_reject": (p.quality_reject if state == "potential" else None)}   # potential: why not yet confirmed

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
