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
                 trigger_tf: str = "1m", refine_tf: str | None = None, min_stop: float | None = None,
                 anchor_tf: str | None = None):
        self.context_tf, self.setup_tf = context_tf, setup_tf
        self.confirm_tf, self.trigger_tf = confirm_tf, trigger_tf
        self.refine_tf = refine_tf     # None = MTF entry-refinement OFF (default); else the entry TF
        self.min_stop = min_stop       # degenerate-stop floor (price), used with refinement
        self.anchor_tf = anchor_tf     # None = no Daily/Weekly anchor (default); else "D"/"W"
        self.context = None            # HTFContext — fixed until the next 4H close
        self.setup = None              # 1H MTFSetup (gated by context) — fixed until the next 1H close
        self.confirmation = None       # 15m MTFSetup (its own gated setup) — fixed until the next 15m close
        self.execution = None          # 1m LTFExecution (the final trigger)

    def on_context_close(self, bars, anchor_bars=None):
        """4H context. If a Daily/Weekly anchor is configured, its bias vetoes a counter-trend 4H
        bias to neutral (trade only with the higher timeframe)."""
        anchor = (P.htf_bias_of(anchor_bars, self.anchor_tf)
                  if (self.anchor_tf and anchor_bars) else "")
        self.context = P.htf_context(bars, self.context_tf, anchor=anchor,
                                     anchor_tf=(self.anchor_tf if anchor else ""))
        return self.context

    def on_setup_close(self, bars, refine_bars=None):
        if self.context is None:
            return None
        rb = refine_bars if self.refine_tf else None      # only refine when the mode is enabled
        self.setup = P.mtf_setup(bars, self.setup_tf, self.context, refine_bars=rb, min_stop=self.min_stop)
        return self.setup

    def on_confirm_close(self, bars, refine_bars=None):
        """15m confirmation: its OWN actionable setup that must confirm the 1H setup (same direction as
        a gated 1H setup). Computed whenever a context exists (so the dashboard can show what is
        developing and WHY each 15m candidate is rejected); it only promotes when the 1H setup is gated.
        When refinement is on, the 15m entry FVG is refined onto the lower TF too."""
        if self.context is None:
            return None
        rb = refine_bars if self.refine_tf else None
        self.confirmation = P.confirm_setup(bars, self.confirm_tf, self.context, self.setup,
                                            refine_bars=rb, min_stop=self.min_stop)
        return self.confirmation

    def on_trigger_close(self, bars):
        """1m trigger — fires only when the full cascade holds; otherwise a NO-TRADE that says how far
        the cascade got. The 1m entry is already the finest TF (no lower to refine onto); the
        degenerate-stop floor still applies via min_stop."""
        self.execution = P.execution_for(bars, self.trigger_tf, self.context, self.setup,
                                         self.confirmation, min_stop=self.min_stop)
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
