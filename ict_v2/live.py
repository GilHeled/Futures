"""ICT v2 — live driver: feed the SAME 1-minute bars the existing feed produces, reuse the existing
`BarBuilder` to resample them (no new pipeline), and drive `MTFEngine` with per-timeframe cadence:

    context 4H  ->  setup 1H  ->  confirmation 15m  ->  execution 1m

`push_1m(bar)` is fed the raw 1m stream. The BarBuilder emits 15m / 1H / 4H closes as they complete;
each drives its layer only on its own close (context 4H, setup 1H, confirmation 15m), while the 1m
execution trigger re-evaluates every bar against the current fixed cascade. State is snapshot-able /
persistable for the dashboard. Advisory only; v1 imported READ-ONLY and untouched.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from ict_live.market.bar_builder import BarBuilder
from ict_v2.engine import MTFEngine
from ict_v2 import entry_models as EM
from ict_v2 import pdarrays                          # NWOG/ORG PD-array context detectors (Lessons 13/14)
from ict_v2.pipeline import session_of              # ET/DST-safe session+killzone (§11, context)

_WINDOW = 240          # context/setup/confirmation structural window
_EXEC_WINDOW = 400     # recent 1m bars for the execution trigger
_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")


def _et_iso(dt):
    """All `updated` timestamps in ONE timezone (ET) so the 4H/1H/15m (ET-aligned resamples) and the 1m
    (ingested UTC) never disagree on wall clock. Naive datetimes are assumed UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_ET).isoformat()


def _px(x):
    """Round a price to 2 decimals for display (raw engine floats carry full binary precision)."""
    return None if x is None else round(float(x), 2)


class V2Live:
    def __init__(self, context_tf: str = "4H", setup_tf: str = "1H", confirm_tf: str = "15m",
                 trigger_tf: str = "1m", window: int = _WINDOW, exec_window: int = _EXEC_WINDOW,
                 refine_tf: str | None = None, min_stop: float | None = None,
                 anchor_tf: str | None = None, entry_models=None):
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.confirm_tf, self.trigger_tf = confirm_tf, trigger_tf
        self.window, self.exec_window = window, exec_window
        self.refine_tf = refine_tf                           # None = MTF entry-refinement OFF (default)
        self.anchor_tf = anchor_tf                           # None = no Daily/Weekly anchor (default); else "D"/"W"
        self.entry_models = entry_models                     # None = FVG only (default execution model)
        # build every TF we need: intraday (5m/15m/1H/4H) + optional Daily/Weekly anchor (D/W need the
        # session calendar, which BarBuilder supplies by default)
        built = tuple(dict.fromkeys(tf for tf in (setup_tf, context_tf, confirm_tf, refine_tf, anchor_tf)
                                    if tf and tf != "1m"))
        self.builder = BarBuilder(timeframes=built)
        self.engine = MTFEngine(context_tf, setup_tf, confirm_tf, trigger_tf,
                                refine_tf=refine_tf, min_stop=min_stop, anchor_tf=anchor_tf,
                                entry_models=entry_models)
        tfs = tuple(dict.fromkeys(tf for tf in (context_tf, setup_tf, confirm_tf, trigger_tf,
                                                refine_tf, anchor_tf) if tf))
        self.buf = {tf: [] for tf in tfs}
        self.updated = {tf: None for tf in tfs}              # last-update time per timeframe (ISO)

    def _append(self, tf, bar, cap):
        b = self.buf[tf]
        b.append(bar)
        if len(b) > cap:
            del b[:-cap]

    def push_1m(self, bar):
        """Feed one 1m bar. Order: [Daily/Weekly anchor] → context (4H) → setup (1H) → confirmation
        (15m) → trigger (1m)."""
        closed = self.builder.add_1m(bar)
        if self.anchor_tf and self.anchor_tf not in (self.context_tf, self.setup_tf, self.confirm_tf):
            for cb in closed:                                # Daily/Weekly anchor — update BEFORE context
                if cb.timeframe == self.anchor_tf:
                    self._append(self.anchor_tf, cb, self.window)
                    self.updated[self.anchor_tf] = _et_iso(cb.close_time)
        for cb in closed:                                    # context (optionally anchored by Daily/Weekly)
            if cb.timeframe == self.context_tf:
                self._append(self.context_tf, cb, self.window)
                ab = self.buf.get(self.anchor_tf) if self.anchor_tf else None
                self.engine.on_context_close(self.buf[self.context_tf], anchor_bars=ab)
                self.updated[self.context_tf] = _et_iso(cb.close_time)
        if self.refine_tf and self.refine_tf not in (self.context_tf, self.setup_tf, self.confirm_tf):
            for cb in closed:                                # refine TF (e.g. 5m) — update BEFORE setup
                if cb.timeframe == self.refine_tf:
                    self._append(self.refine_tf, cb, self.exec_window)
                    self.updated[self.refine_tf] = _et_iso(cb.close_time)
        for cb in closed:                                    # setup (optionally entry-refined on refine_tf)
            if cb.timeframe == self.setup_tf:
                self._append(self.setup_tf, cb, self.window)
                rb = self.buf.get(self.refine_tf) if self.refine_tf else None
                self.engine.on_setup_close(self.buf[self.setup_tf], refine_bars=rb)
                self.updated[self.setup_tf] = _et_iso(cb.close_time)
        for cb in closed:                                    # confirmation (optionally entry-refined too)
            if cb.timeframe == self.confirm_tf:
                self._append(self.confirm_tf, cb, self.window)
                rb = self.buf.get(self.refine_tf) if self.refine_tf else None
                self.engine.on_confirm_close(self.buf[self.confirm_tf], refine_bars=rb)
                self.updated[self.confirm_tf] = _et_iso(cb.close_time)
        self._append(self.trigger_tf, bar, self.exec_window)  # 1m trigger = every bar
        self.engine.on_trigger_close(self.buf[self.trigger_tf])
        self.updated[self.trigger_tf] = _et_iso(bar.close_time)
        return self.engine.state()

    # ---- serialization ------------------------------------------------------------------------
    @staticmethod
    def _setup_dict(mtf) -> Optional[dict]:
        if mtf is None:
            return None
        g0 = mtf.gated[0] if mtf.gated else None
        obj = g0.objective if (g0 and g0.objective is not None) else None
        ci = mtf.cand_info
        return {"available": len(mtf.candidates), "gated": len(mtf.gated), "total": len(ci),
                "passed": sum(1 for x in ci if x.get("status") == "passed"),
                "incomplete": sum(1 for x in ci if x.get("status") == "incomplete"),
                "rejected": sum(1 for x in ci if x.get("status") == "rejected"),
                "direction": (g0.setup.direction if g0 else None),
                "top": None if not g0 else {"direction": g0.setup.direction, "entry": _px(g0.setup.entry),
                                            "stop": _px(g0.setup.stop)},
                "objective": None if obj is None else {"kind": obj.kind, "price": _px(obj.price)},
                "candidates": list(mtf.cand_info)}      # full list for the drill-down popup

    def snapshot(self) -> dict:
        eng = self.engine
        c, s, cf, e = eng.context, eng.setup, eng.confirmation, eng.execution
        dr = getattr(c, "dealing_range", None) if c else None
        obj = None
        if s and s.gated and s.gated[0].objective is not None:
            p = s.gated[0].objective
            obj = {"kind": p.kind, "price": _px(p.price)}
        top = e.executables[0] if (e and e.executables) else None
        def _dr(r):                                               # serialize a dealing range (source_tf-tagged)
            return None if r is None else {"tf": getattr(r, "source_tf", ""), "low": _px(r.low),
                                           "high": _px(r.high), "ce": _px(r.ce), "direction": r.direction}
        nested = [d for d in (_dr(getattr(c, "dealing_range", None)),      # §5/§6 nested hierarchy: 4H⊃1H⊃15m
                              _dr(getattr(s, "dealing_range", None)),
                              _dr(getattr(cf, "dealing_range", None))) if d is not None]
        pools = [{"kind": "BSL" if getattr(p, "kind", None) == "high" else "SSL", "price": _px(p.price)}
                 for p in (c.liquidity if c else [])]             # §3 the FULL pool set (BSL/SSL)
        fib = c.fib_levels() if c else []                          # §6 fib ladder (Lesson 8)
        nwog = pdarrays.nwogs(self.buf.get(self.context_tf) or [])  # §18 New Week Opening Gaps (Lesson 13)
        org = pdarrays.org(self.buf.get(self.confirm_tf) or [])     # §19 Opening Range Gap (Lesson 14; 15m has 09:30/16:15)
        tb = self.buf.get(self.trigger_tf) or []                  # last 1m bar = the latest price
        last = tb[-1] if tb else None
        last_dir = None
        sess = kz = ""
        if last is not None:
            last_dir = "up" if last.close > last.open else "down" if last.close < last.open else "flat"
            sess, kz = session_of(last.close_time)          # current session/killzone (§11 context)
        return {
            "session": sess, "killzone": kz,                # "it is now …" — context, not a gate
            "timeframes": {"context": self.context_tf, "setup": self.setup_tf,
                           "confirm": self.confirm_tf, "trigger": self.trigger_tf,
                           "refine": self.refine_tf, "anchor": self.anchor_tf},
            "entry_models": {"enabled": list(EM.resolve(self.entry_models)), "catalog": EM.catalog()},
            "updated": dict(self.updated),
            "last": None if last is None else {"price": _px(last.close), "dir": last_dir,
                                               "time": _et_iso(last.close_time)},
            "context": None if not c else {
                "bias": c.bias, "anchor_bias": getattr(c, "anchor_bias", ""),
                "anchor_tf": getattr(c, "anchor_tf", ""),
                "dealing_range": None if dr is None else {"low": _px(dr.low), "high": _px(dr.high),
                                                          "ce": _px(dr.ce), "direction": dr.direction},
                "fib": fib,                                   # §6 fib ladder 0/0.5/0.62/0.79/1 (Lesson 8)
                "pools": pools,                               # §3 full ERL pool set (BSL/SSL), Lesson 6
                "nwog": nwog,                                 # §18 New Week Opening Gaps (Lesson 13) — S/R + magnet
                "org": org,                                   # §19 Opening Range Gap (Lesson 14) — current-day, 50% line
                "liquidity_draws": len(c.liquidity), "liquidity_objective": obj},
            "dealing_ranges": nested,                         # §5/§6 nested range hierarchy (source_tf-tagged)
            "setup": self._setup_dict(s),
            "confirmation": self._setup_dict(cf),
            "execution": None if not e else {
                "decision": e.decision, "executables": len(e.executables), "fvgs": len(e.fvgs),
                "available": sum(1 for x in e.cand_info if x.get("actionable")),
                "gated": sum(1 for x in e.cand_info if x.get("passed")),
                "passed": sum(1 for x in e.cand_info if x.get("status") == "passed"),
                "incomplete": sum(1 for x in e.cand_info if x.get("status") == "incomplete"),
                "rejected": sum(1 for x in e.cand_info if x.get("status") == "rejected"),
                "total": len(e.cand_info), "candidates": list(e.cand_info),
                "top": None if top is None else {"direction": top.direction, "entry": _px(top.entry),
                                                 "stop": _px(top.stop), "target": _px(top.target),
                                                 "ltf_confirmed": top.ltf_confirmed}},
        }

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.snapshot(), default=str))


def run_bars(one_min_bars, *, out: Optional[str] = None, **kw) -> V2Live:
    """Replay a 1m bar sequence through V2Live (e.g. the shared raw-1m store or a Databento sample)."""
    v = V2Live(**kw)
    for b in one_min_bars:
        v.push_1m(b)
    if out:
        v.save(out)
    return v
