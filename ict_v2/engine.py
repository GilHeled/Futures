"""ICT v2 — stateful multi-timeframe engine with per-layer CADENCE.

Each layer recomputes only when ITS timeframe closes, so the execution layer can react to every
lower-timeframe close while the higher-timeframe context stays fixed until its own close:

    on_htf_close(htf_bars)   -> recompute HTF context   (fixed until the next HTF close)
    on_mtf_close(mtf_bars)   -> recompute MTF setup+gate (against the current context)
    on_ltf_close(ltf_bars)   -> recompute LTF execution  (against the current setup+context)

This removes v1's "one evaluation per HTF bar" limitation — a valid LTF entry into a standing,
HTF-gated setup fires on the LTF close, not only when the HTF bar closes. Reuses the stage functions
in `pipeline` (which reuse the frozen v1 engine read-only).
"""
from __future__ import annotations

from ict_v2 import pipeline as P


class MTFEngine:
    def __init__(self, htf: str = "4H", mtf: str = "15m", ltf: str = "1m"):
        self.htf, self.mtf, self.ltf = htf, mtf, ltf
        self.context = None        # HTFContext — fixed until the next HTF close
        self.setup = None          # MTFSetup (gated by the current context) — fixed until the next MTF close
        self.execution = None      # LTFExecution — refreshed on every LTF close

    def on_htf_close(self, htf_bars):
        """A higher-timeframe bar closed → recompute the context. (Setup/execution keep their last
        value until their own timeframes close.)"""
        self.context = P.htf_context(htf_bars, self.htf)
        return self.context

    def on_mtf_close(self, mtf_bars):
        """An intermediate-timeframe bar closed → recompute the setup and re-gate it against the
        CURRENT (fixed) context. No-op until a context exists."""
        if self.context is None:
            return None
        self.setup = P.mtf_setup(mtf_bars, self.mtf, self.context)
        return self.setup

    def on_ltf_close(self, ltf_bars):
        """A lower-timeframe bar closed → re-evaluate execution against the CURRENT (fixed)
        context + setup. This is the fast cadence: it can fire between HTF/MTF closes."""
        if self.setup is None:
            self.execution = P.LTFExecution(tf=self.ltf, decision="NO-TRADE (no setup yet)")
        else:
            self.execution = P.ltf_execution(ltf_bars, self.ltf, self.setup, self.context)
        return self.execution

    def state(self) -> P.MTFState:
        return P.MTFState(context=self.context, setup=self.setup, execution=self.execution)


def _demo() -> None:
    base = P._base_1m(20000, 7)
    htf, mtf = P.resample(base, 240, "4H"), P.resample(base, 15, "15m")
    eng = MTFEngine("4H", "15m", "1m")
    eng.on_htf_close(htf)
    eng.on_mtf_close(mtf)
    print("ICT v2 — LTF cadence (HTF context fixed; execution reacts per LTF close)\n")
    print(f"HTF context: bias={eng.context.bias}  gated setups={len(eng.setup.gated)}\n")
    ctx_id = id(eng.context)
    for i, end in enumerate((300, 340, 380, 400)):        # successive LTF closes
        e = eng.on_ltf_close(base[:end][-400:])
        fixed = "context FIXED" if id(eng.context) == ctx_id else "context changed!"
        print(f"  LTF close #{i+1} (bar {end}): decision={e.decision}  executables={len(e.executables)}  [{fixed}]")


if __name__ == "__main__":
    _demo()
