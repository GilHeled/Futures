"""ICT v2 — live driver: feed the SAME 1-minute bars the existing feed produces, reuse the existing
`BarBuilder` to resample them to HTF/MTF, and drive `MTFEngine` with per-timeframe cadence.

No new data pipeline: `push_1m(bar)` is fed the raw 1m stream (from the shared store / feed). Each 1m
bar is the LTF close; the BarBuilder emits MTF (15m) and HTF (4H) closes as they complete. Cadence is
honored top-down — HTF context on a 4H close, MTF setup on a 15m close, LTF execution on every 1m —
so context refreshes only on its own close while execution reacts each minute.

State (context / setup / execution + last-update time per timeframe) is snapshot-able and persistable
so a dashboard can show it. Advisory only; v1 (`ict_live/`) is imported READ-ONLY and untouched.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ict_live.market.bar_builder import BarBuilder
from ict_v2.engine import MTFEngine

_WINDOW = 240          # HTF/MTF structural window
_LTF_WINDOW = 400      # recent LTF bars for execution


class V2Live:
    def __init__(self, htf: str = "4H", mtf: str = "15m", ltf: str = "1m",
                 window: int = _WINDOW, ltf_window: int = _LTF_WINDOW):
        self.htf, self.mtf, self.ltf = htf, mtf, ltf
        self.window, self.ltf_window = window, ltf_window
        self.builder = BarBuilder(timeframes=(mtf, htf))     # reuse the existing bar builder (1m -> 15m/4H)
        self.engine = MTFEngine(htf, mtf, ltf)
        self.buf = {htf: [], mtf: [], ltf: []}
        self.updated = {htf: None, mtf: None, ltf: None}     # last-update time per timeframe (ISO)

    def _append(self, tf, bar, cap):
        b = self.buf[tf]
        b.append(bar)
        if len(b) > cap:
            del b[:-cap]

    def push_1m(self, bar):
        """Feed one 1m bar. Drives HTF context (on a 4H close) → MTF setup (on a 15m close) →
        LTF execution (every 1m), in that order. Returns the current three-stage state."""
        closed = self.builder.add_1m(bar)
        for cb in closed:                                    # HTF first (context before setup)
            if cb.timeframe == self.htf:
                self._append(self.htf, cb, self.window)
                self.engine.on_htf_close(self.buf[self.htf])
                self.updated[self.htf] = cb.close_time.isoformat()
        for cb in closed:
            if cb.timeframe == self.mtf:
                self._append(self.mtf, cb, self.window)
                self.engine.on_mtf_close(self.buf[self.mtf])
                self.updated[self.mtf] = cb.close_time.isoformat()
        self._append(self.ltf, bar, self.ltf_window)         # LTF close = every 1m bar
        self.engine.on_ltf_close(self.buf[self.ltf])
        self.updated[self.ltf] = bar.close_time.isoformat()
        return self.engine.state()

    # ---- serialization for persistence / dashboard --------------------------------------------
    def snapshot(self) -> dict:
        eng = self.engine
        c, s, e = eng.context, eng.setup, eng.execution
        dr = getattr(c, "dealing_range", None) if c else None
        obj = None
        if s and s.gated and s.gated[0].objective is not None:
            p = s.gated[0].objective
            obj = {"kind": p.kind, "price": p.price}
        top = e.executables[0] if (e and e.executables) else None
        return {
            "timeframes": {"htf": self.htf, "mtf": self.mtf, "ltf": self.ltf},
            "updated": dict(self.updated),
            "context": None if not c else {
                "bias": c.bias,
                "dealing_range": None if dr is None else {"low": dr.low, "high": dr.high,
                                                          "ce": dr.ce, "direction": dr.direction},
                "liquidity_draws": len(c.liquidity),
                "liquidity_objective": obj,
            },
            "setup": None if not s else {
                "candidates": len(s.candidates), "gated": len(s.gated),
                "top": None if not s.gated else {
                    "direction": s.gated[0].setup.direction,
                    "entry": s.gated[0].setup.entry, "stop": s.gated[0].setup.stop},
            },
            "execution": None if not e else {
                "decision": e.decision, "executables": len(e.executables), "fvgs": len(e.fvgs),
                "top": None if top is None else {
                    "direction": top.direction, "entry": top.entry, "stop": top.stop,
                    "target": top.target, "ltf_confirmed": top.ltf_confirmed},
            },
        }

    def save(self, path) -> None:
        Path(path).write_text(json.dumps(self.snapshot(), default=str))


def run_bars(one_min_bars, *, htf="4H", mtf="15m", ltf="1m", out: Optional[str] = None) -> V2Live:
    """Replay a 1m bar sequence through V2Live (e.g. the shared raw-1m store or a Databento sample)."""
    v = V2Live(htf, mtf, ltf)
    for b in one_min_bars:
        v.push_1m(b)
    if out:
        v.save(out)
    return v
