"""ICT v2 — live driver: feed the SAME 1-minute bars the existing feed produces, reuse the existing
`BarBuilder` to resample them (no new pipeline), and drive `MTFEngine` with per-timeframe cadence:

    context 4H  ->  setup 1H  ->  execution 15m -> 1m

`push_1m(bar)` is fed the raw 1m stream. The BarBuilder emits 15m / 1H / 4H closes as they complete;
each drives its layer only on its own close (context on 4H, setup on 1H), while execution re-evaluates
on every 15m close and every 1m close against the current fixed context+setup. State is snapshot-able
/ persistable for the dashboard. Advisory only; v1 imported READ-ONLY and untouched.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ict_live.market.bar_builder import BarBuilder
from ict_v2.engine import MTFEngine

_WINDOW = 240          # context/setup structural window
_EXEC_WINDOW = 400     # recent bars per exec TF


class V2Live:
    def __init__(self, context_tf: str = "4H", setup_tf: str = "1H", exec_tfs=("15m", "1m"),
                 window: int = _WINDOW, exec_window: int = _EXEC_WINDOW):
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.exec_tfs = tuple(exec_tfs)
        self.window, self.exec_window = window, exec_window
        built = tuple(dict.fromkeys(tf for tf in (setup_tf, context_tf, *self.exec_tfs) if tf != "1m"))
        self.builder = BarBuilder(timeframes=built)          # reuse existing bar builder (1m -> 15m/1H/4H)
        self.engine = MTFEngine(context_tf, setup_tf, self.exec_tfs)
        tfs = (context_tf, setup_tf, *self.exec_tfs)
        self.buf = {tf: [] for tf in tfs}
        self.updated = {tf: None for tf in tfs}              # last-update time per timeframe (ISO)

    def _append(self, tf, bar, cap):
        b = self.buf[tf]
        b.append(bar)
        if len(b) > cap:
            del b[:-cap]

    def push_1m(self, bar):
        """Feed one 1m bar. Order: context (4H) → setup (1H) → exec (15m, then 1m every bar)."""
        closed = self.builder.add_1m(bar)
        for cb in closed:                                    # context first
            if cb.timeframe == self.context_tf:
                self._append(self.context_tf, cb, self.window)
                self.engine.on_context_close(self.buf[self.context_tf])
                self.updated[self.context_tf] = cb.close_time.isoformat()
        for cb in closed:                                    # then setup
            if cb.timeframe == self.setup_tf:
                self._append(self.setup_tf, cb, self.window)
                self.engine.on_setup_close(self.buf[self.setup_tf])
                self.updated[self.setup_tf] = cb.close_time.isoformat()
        for cb in closed:                                    # then coarser exec TFs (e.g. 15m)
            if cb.timeframe in self.exec_tfs and cb.timeframe != "1m":
                self._append(cb.timeframe, cb, self.exec_window)
                self.engine.on_exec_close(cb.timeframe, self.buf[cb.timeframe])
                self.updated[cb.timeframe] = cb.close_time.isoformat()
        if "1m" in self.exec_tfs:                            # 1m exec = every bar
            self._append("1m", bar, self.exec_window)
            self.engine.on_exec_close("1m", self.buf["1m"])
            self.updated["1m"] = bar.close_time.isoformat()
        return self.engine.state()

    # ---- serialization ------------------------------------------------------------------------
    @staticmethod
    def _exec_dict(ex) -> Optional[dict]:
        if ex is None:
            return None
        top = ex.executables[0] if ex.executables else None
        return {"tf": ex.tf, "decision": ex.decision, "executables": len(ex.executables),
                "fvgs": len(ex.fvgs),
                "top": None if top is None else {"direction": top.direction, "entry": top.entry,
                                                 "stop": top.stop, "target": top.target,
                                                 "ltf_confirmed": top.ltf_confirmed}}

    def snapshot(self) -> dict:
        eng = self.engine
        c, s = eng.context, eng.setup
        dr = getattr(c, "dealing_range", None) if c else None
        obj = None
        if s and s.gated and s.gated[0].objective is not None:
            p = s.gated[0].objective
            obj = {"kind": p.kind, "price": p.price}
        return {
            "timeframes": {"context": self.context_tf, "setup": self.setup_tf, "exec": list(self.exec_tfs)},
            "updated": dict(self.updated),
            "context": None if not c else {
                "bias": c.bias,
                "dealing_range": None if dr is None else {"low": dr.low, "high": dr.high,
                                                          "ce": dr.ce, "direction": dr.direction},
                "liquidity_draws": len(c.liquidity), "liquidity_objective": obj},
            "setup": None if not s else {
                "candidates": len(s.candidates), "gated": len(s.gated),
                "top": None if not s.gated else {"direction": s.gated[0].setup.direction,
                                                 "entry": s.gated[0].setup.entry,
                                                 "stop": s.gated[0].setup.stop}},
            "execution": {tf: self._exec_dict(eng.executions.get(tf)) for tf in self.exec_tfs},
            "current": self._exec_dict(eng.current_execution()),
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
