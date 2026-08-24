"""ICT v2 — stateful four-layer cascade with per-timeframe CADENCE, mapped to the course workflow:

    context 4H  ->  setup 1H  ->  confirmation 15m  ->  execution 1m

    on_context_close(bars) -> 4H context  (bias, dealing range, liquidity draw)
    on_setup_close(bars)   -> 1H setup     (manipulation/displacement/MSS/FVG, gated by context)
    on_confirm_close(bars) -> 15m confirmation (its OWN sweep/MSS/displacement/FVG, in the setup
                              direction — confirms the 1H setup is developing)
    on_trigger_close(bars) -> 1m execution (the final trigger; fires only when the whole cascade holds:
                              context bias -> gated 1H setup -> gated 15m confirmation -> 1m entry FVG)

Each layer recomputes only when ITS timeframe closes; higher layers stay fixed until their own close.
Reuses the stage functions in `pipeline` (which reuse the frozen v1 engine read-only).
"""
from __future__ import annotations

from ict_v2 import pipeline as P


class MTFEngine:
    def __init__(self, context_tf: str = "4H", setup_tf: str = "1H", confirm_tf: str = "15m",
                 trigger_tf: str = "1m"):
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.confirm_tf, self.trigger_tf = confirm_tf, trigger_tf
        self.context = None            # HTFContext — fixed until the next 4H close
        self.setup = None              # 1H MTFSetup (gated by context) — fixed until the next 1H close
        self.confirmation = None       # 15m MTFSetup (its own gated setup) — fixed until the next 15m close
        self.execution = None          # 1m LTFExecution (the final trigger)

    def on_context_close(self, bars):
        self.context = P.htf_context(bars, self.context_tf)
        return self.context

    def on_setup_close(self, bars):
        if self.context is None:
            return None
        self.setup = P.mtf_setup(bars, self.setup_tf, self.context)
        return self.setup

    def on_confirm_close(self, bars):
        """15m confirmation: its OWN actionable setup that must confirm the 1H setup (same direction as
        a gated 1H setup). Computed whenever a context exists (so the dashboard can show what is
        developing and WHY each 15m candidate is rejected); it only promotes when the 1H setup is gated."""
        if self.context is None:
            return None
        self.confirmation = P.confirm_setup(bars, self.confirm_tf, self.context, self.setup)
        return self.confirmation

    def on_trigger_close(self, bars):
        """1m trigger — fires only when the full cascade holds; otherwise a NO-TRADE that says how far
        the cascade got."""
        self.execution = P.execution_for(bars, self.trigger_tf, self.context, self.setup, self.confirmation)
        return self.execution

    def state(self) -> P.MTFState:
        return P.MTFState(context=self.context, setup=self.setup, confirmation=self.confirmation,
                          execution=self.execution)


def _demo() -> None:
    base = P._base_1m(20000, 7)
    h4, h1, m15 = P.resample(base, 240, "4H"), P.resample(base, 60, "1H"), P.resample(base, 15, "15m")
    eng = MTFEngine("4H", "1H", "15m", "1m")
    eng.on_context_close(h4)
    eng.on_setup_close(h1)
    eng.on_confirm_close(m15)
    eng.on_trigger_close(base[-400:])
    print("ICT v2 — 4H context -> 1H setup -> 15m confirmation -> 1m execution\n")
    print(eng.state().describe())


if __name__ == "__main__":
    _demo()
