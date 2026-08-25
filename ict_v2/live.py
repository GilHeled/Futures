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
                 trigger_tf: str = "1m", window: int = _WINDOW, exec_window: int = _EXEC_WINDOW):
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.confirm_tf, self.trigger_tf = confirm_tf, trigger_tf
        self.window, self.exec_window = window, exec_window
        built = tuple(dict.fromkeys(tf for tf in (setup_tf, context_tf, confirm_tf) if tf != "1m"))
        self.builder = BarBuilder(timeframes=built)          # reuse existing bar builder (1m -> 15m/1H/4H)
        self.engine = MTFEngine(context_tf, setup_tf, confirm_tf, trigger_tf)
        tfs = (context_tf, setup_tf, confirm_tf, trigger_tf)
        self.buf = {tf: [] for tf in tfs}
        self.updated = {tf: None for tf in tfs}              # last-update time per timeframe (ISO)

    def _append(self, tf, bar, cap):
        b = self.buf[tf]
        b.append(bar)
        if len(b) > cap:
            del b[:-cap]

    def push_1m(self, bar):
        """Feed one 1m bar. Order: context (4H) → setup (1H) → confirmation (15m) → trigger (1m)."""
        closed = self.builder.add_1m(bar)
        for cb in closed:                                    # context
            if cb.timeframe == self.context_tf:
                self._append(self.context_tf, cb, self.window)
                self.engine.on_context_close(self.buf[self.context_tf])
                self.updated[self.context_tf] = _et_iso(cb.close_time)
        for cb in closed:                                    # setup
            if cb.timeframe == self.setup_tf:
                self._append(self.setup_tf, cb, self.window)
                self.engine.on_setup_close(self.buf[self.setup_tf])
                self.updated[self.setup_tf] = _et_iso(cb.close_time)
        for cb in closed:                                    # confirmation
            if cb.timeframe == self.confirm_tf:
                self._append(self.confirm_tf, cb, self.window)
                self.engine.on_confirm_close(self.buf[self.confirm_tf])
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
        tb = self.buf.get(self.trigger_tf) or []                  # last 1m bar = the latest price
        last = tb[-1] if tb else None
        last_dir = None
        if last is not None:
            last_dir = "up" if last.close > last.open else "down" if last.close < last.open else "flat"
        return {
            "timeframes": {"context": self.context_tf, "setup": self.setup_tf,
                           "confirm": self.confirm_tf, "trigger": self.trigger_tf},
            "updated": dict(self.updated),
            "last": None if last is None else {"price": _px(last.close), "dir": last_dir,
                                               "time": _et_iso(last.close_time)},
            "context": None if not c else {
                "bias": c.bias,
                "dealing_range": None if dr is None else {"low": _px(dr.low), "high": _px(dr.high),
                                                          "ce": _px(dr.ce), "direction": dr.direction},
                "liquidity_draws": len(c.liquidity), "liquidity_objective": obj},
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
