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
                 anchor_tf: str | None = None, entry_models=None, point_value: float | None = None,
                 price_dp: int = 2):
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.confirm_tf, self.trigger_tf = confirm_tf, trigger_tf
        self.window, self.exec_window = window, exec_window
        self.refine_tf = refine_tf                           # None = MTF entry-refinement OFF (default)
        self.anchor_tf = anchor_tf                           # None = no Daily/Weekly anchor (default); else "D"/"W"
        self.entry_models = entry_models                     # None = FVG only (default execution model)
        self.point_value = point_value                       # $ per point (1 contract) — for $ risk/reward on the card
        # build every TF we need: intraday (5m/15m/1H/4H) + optional Daily/Weekly anchor (D/W need the
        # session calendar, which BarBuilder supplies by default)
        built = tuple(dict.fromkeys(tf for tf in (setup_tf, context_tf, confirm_tf, refine_tf, anchor_tf)
                                    if tf and tf != "1m"))
        self.builder = BarBuilder(timeframes=built)
        self.engine = MTFEngine(context_tf, setup_tf, confirm_tf, trigger_tf,
                                refine_tf=refine_tf, min_stop=min_stop, anchor_tf=anchor_tf,
                                entry_models=entry_models, point_value=point_value)
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
    def _context_dict(self, c, *, gaps: bool) -> Optional[dict]:
        """Serialize a context stage (4H strategic / 1H intraday) — context ONLY, never candidates.
        `gaps` adds NWOG/ORG (strategic 4H). Each pool/gap is tagged ERL/IRL; gaps get a contextual role."""
        if c is None:
            return None
        dr = getattr(c, "dealing_range", None)
        pools = [{"kind": "BSL" if getattr(p, "kind", None) == "high" else "SSL", "price": _px(p.price),
                  "loc": (c.erl_irl(p.price))} for p in (c.liquidity or [])]
        nwog = org = None
        if gaps:
            def _role_gap(g, adapt):
                g["loc"] = c.erl_irl(g["mid"])
                arr = adapt(g)
                pdarrays.role_of(arr, direction=(c.bias or ""), zone=c.zone(g["mid"]), erl_irl=g["loc"])
                g["role"], g["role_basis"] = arr.role, arr.role_basis
            nwog = pdarrays.nwogs(self.buf.get(self.context_tf) or [])   # Lesson 13
            for g in nwog:
                _role_gap(g, pdarrays.from_nwog)
            org = pdarrays.org(self.buf.get(self.confirm_tf) or [])      # Lesson 14 (15m has 09:30/16:15)
            if org:
                _role_gap(org, pdarrays.from_org)
        return {
            "tf": getattr(c, "tf", ""),
            "bias": c.bias, "anchor_bias": getattr(c, "anchor_bias", ""), "anchor_tf": getattr(c, "anchor_tf", ""),
            "trend": getattr(c, "trend", "none"), "trend_change": getattr(c, "trend_change", ""),
            "dealing_range": None if dr is None else {"low": _px(dr.low), "high": _px(dr.high),
                                                      "ce": _px(dr.ce), "direction": dr.direction},
            "fib": c.fib_levels(), "pools": pools,
            "draws": [d.to_dict() for d in getattr(c, "draws", [])],
            **({"nwog": nwog, "org": org} if gaps else {}),
        }

    def _bars(self, tf, n):
        """Recent OHLC for the per-scenario candlestick chart (last n bars of a timeframe)."""
        return [{"t": _et_iso(b.close_time), "o": _px(b.open), "h": _px(b.high),
                 "l": _px(b.low), "c": _px(b.close)} for b in (self.buf.get(tf) or [])[-n:]]

    def snapshot(self) -> dict:
        eng = self.engine
        strategic, intraday = eng.strategic, eng.intraday
        def _dr(r):
            return None if r is None else {"tf": getattr(r, "source_tf", ""), "low": _px(r.low),
                                           "high": _px(r.high), "ce": _px(r.ce), "direction": r.direction}
        nested = [d for d in (_dr(getattr(strategic, "dealing_range", None)),   # nested hierarchy 4H⊃1H
                              _dr(getattr(intraday, "dealing_range", None))) if d is not None]
        tb = self.buf.get(self.trigger_tf) or []
        last = tb[-1] if tb else None
        last_dir = sess = kz = ""
        if last is not None:
            last_dir = "up" if last.close > last.open else "down" if last.close < last.open else "flat"
            sess, kz = session_of(last.close_time)
        scenarios = eng.book.to_list()
        return {
            "session": sess, "killzone": kz,
            "timeframes": {"context": self.context_tf, "setup": self.setup_tf,
                           "confirm": self.confirm_tf, "trigger": self.trigger_tf,
                           "refine": self.refine_tf, "anchor": self.anchor_tf},
            "entry_models": {"enabled": list(EM.resolve(self.entry_models)), "catalog": EM.catalog()},
            "updated": dict(self.updated),
            "last": None if last is None else {"price": _px(last.close), "dir": last_dir,
                                               "time": _et_iso(last.close_time)},
            # H4 = strategic context · H1 = intraday context (context only — never candidates)
            "strategic": self._context_dict(strategic, gaps=True),
            "intraday": self._context_dict(intraday, gaps=False),
            "dealing_ranges": nested,
            # the 2-3 stable market theses + their execution state (the primary view)
            "scenarios": scenarios,
            "scenario_summary": {"active": len(scenarios),
                                 "triggered": sum(1 for s in scenarios if s.get("state") == "triggered"),
                                 "armed": sum(1 for s in scenarios if s.get("state") == "armed"),
                                 "retracing": sum(1 for s in scenarios if s.get("state") == "retracing"),
                                 "watching": sum(1 for s in scenarios if s.get("state") == "watching")},
            # trigger→outcome record (the "recommendations + actual results") + session stats
            "scenario_stats": eng.book.stats(),
            "trades": list(eng.book.trades),
            "objectives": [o.to_dict() for o in getattr(eng, "objectives", [])],   # full liquidity inventory
            # recent OHLC per execution TF for the per-scenario candlestick chart (15m default; 1H/1m toggle)
            "bars": {self.confirm_tf: self._bars(self.confirm_tf, 90),
                     self.setup_tf: self._bars(self.setup_tf, 54),
                     self.trigger_tf: self._bars(self.trigger_tf, 120)},
            "point_value": self.point_value,        # $ per point (1 contract) → $ risk/reward on the card
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
