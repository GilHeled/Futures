"""ICT v2 — stateful multi-timeframe engine with per-layer CADENCE, mapped to the course workflow:

    context (4H)  ->  setup (1H)  ->  execution (15m -> 1m)

    on_context_close(bars)   -> recompute 4H context   (bias, dealing range, liquidity draw)
    on_setup_close(bars)     -> recompute 1H setup+gate (manipulation/displacement/MSS vs context)
    on_exec_close(tf, bars)  -> recompute execution on that lower TF (15m or 1m) against the CURRENT
                                context+setup — the step-down the course teaches: once a 1H setup
                                exists, monitor 15m then 1m until an execution appears or the setup
                                is no longer gated.

Each layer only recomputes when ITS timeframe closes; higher layers stay fixed until their own close.
Reuses the stage functions in `pipeline` (which reuse the frozen v1 engine read-only).
"""
from __future__ import annotations

from ict_v2 import pipeline as P


class MTFEngine:
    def __init__(self, context_tf: str = "4H", setup_tf: str = "1H", exec_tfs=("15m", "1m")):
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.exec_tfs = tuple(exec_tfs)                 # ordered coarse->fine, e.g. ("15m","1m")
        self.context = None                             # HTFContext — fixed until the next 4H close
        self.setup = None                               # MTFSetup (gated by context) — fixed until next 1H close
        self.executions = {tf: None for tf in self.exec_tfs}   # LTFExecution per exec TF

    def on_context_close(self, bars):
        """4H bar closed → recompute context (bias, dealing range, liquidity objective)."""
        self.context = P.htf_context(bars, self.context_tf)
        return self.context

    def on_setup_close(self, bars):
        """1H bar closed → recompute the setup and re-gate it against the CURRENT context.
        No-op until a context exists."""
        if self.context is None:
            return None
        self.setup = P.mtf_setup(bars, self.setup_tf, self.context)
        return self.setup

    def on_exec_close(self, tf: str, bars):
        """A lower-TF (15m or 1m) bar closed → re-evaluate execution on that TF against the CURRENT
        (fixed) context + 1H setup. Fires only while a gated 1H setup stands."""
        if self.setup is None:
            self.executions[tf] = P.LTFExecution(tf=tf, decision="NO-TRADE (no setup yet)")
        else:
            self.executions[tf] = P.ltf_execution(bars, tf, self.setup, self.context)
        return self.executions[tf]

    def current_execution(self):
        """The finest exec TF that has an executable (1m preferred over 15m); else the finest TF's
        state (NO-TRADE). This is the 15m->1m step-down: a 15m execution is superseded by a 1m one."""
        for tf in reversed(self.exec_tfs):              # fine -> coarse
            ex = self.executions.get(tf)
            if ex and ex.executables:
                return ex
        return self.executions.get(self.exec_tfs[-1])

    def state(self) -> P.MTFState:
        return P.MTFState(context=self.context, setup=self.setup,
                          execution=self.current_execution(), executions=dict(self.executions))


def _demo() -> None:
    base = P._base_1m(20000, 7)
    ctx4h, s1h, x15 = P.resample(base, 240, "4H"), P.resample(base, 60, "1H"), P.resample(base, 15, "15m")
    eng = MTFEngine("4H", "1H", ("15m", "1m"))
    eng.on_context_close(ctx4h)
    eng.on_setup_close(s1h)
    print("ICT v2 — 4H context -> 1H setup -> 15m/1m execution\n")
    print(f"context: bias={eng.context.bias}  gated 1H setups={len(eng.setup.gated)}\n")
    ctx_id = id(eng.context)
    for tf, bars in (("15m", x15), ("1m", base[-400:])):
        e = eng.on_exec_close(tf, bars)
        fixed = "context FIXED" if id(eng.context) == ctx_id else "context changed!"
        print(f"  exec {tf}: decision={e.decision}  executables={len(e.executables)}  [{fixed}]")


if __name__ == "__main__":
    _demo()
