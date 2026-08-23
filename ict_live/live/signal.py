"""TradeTicket — the complete, ex-ante decision the live system emits when a signal-TF bar closes.

Assembles the three FROZEN layers into one structured record:
  - structural recommendation (engine): LONG / SHORT / NO-TRADE, with entry / stop / structural target
  - execution filter v1: TRADE / PASS + confidence + reasons + weakest factor
  - exit model: the mechanical take-profit at +EXIT_TARGET_R (structural target kept as analytical)

`action` is the single actionable verdict: TAKE (structural direction AND execution TRADE), SKIP
(structural direction but execution PASS), or NO_SETUP (no live setup). Pure function over bar lists,
so it is fully testable without any web server. Adds no trading logic.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from ict_live import config as C
from ict_live.engine import execution_quality as EQ
from ict_live.engine import pipeline
from ict_live.journal import record as REC

WINDOW = 240
_MIN_STOP = 2.0                         # MES/MNQ execution floor (8 ticks * 0.25); see config


@dataclass(frozen=True)
class TradeTicket:
    ticket_id: str
    time: str
    symbol: str
    signal_tf: str
    action: str                         # TAKE / SKIP / NO_SETUP
    structural: str                     # LONG / SHORT / NO-TRADE
    execution: str                      # TRADE / PASS / N/A
    confidence: float
    reasons: tuple = ()
    weakest_factor: str = ""
    entry: Optional[float] = None
    stop: Optional[float] = None
    risk: Optional[float] = None
    exit_target: Optional[float] = None         # mechanical exit (+EXIT_TARGET_R) — the take-profit
    exit_target_R: float = EQ.EXIT_TARGET_R
    structural_target: Optional[float] = None   # analytical objective (NOT the exit)
    structural_rr: Optional[float] = None
    entry_tf: str = ""
    reasoning: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reasons"] = list(self.reasons)
        return d


def _exit_target(direction: str, entry: float, risk: float) -> float:
    r = EQ.EXIT_TARGET_R
    return entry + r * risk if direction == "long" else entry - r * risk


def build_ticket(signal_bars, entry_bars=None, *, symbol: str, signal_tf: str,
                 entry_tf: str = "", window: int = WINDOW, min_stop: float = _MIN_STOP) -> TradeTicket:
    """Run the frozen pipeline on the closed signal-TF window (+ optional lower-TF entry-refine bars)
    and assemble the ticket. `signal_bars` must be closed, chronological, causal (cursor = last bar)."""
    bars = signal_bars[-window:] if window else signal_bars
    ms = pipeline.analyze(bars, signal_tf, refine_bars=entry_bars, min_stop=min_stop)
    ea = EQ.assess(ms)
    last = signal_bars[-1]
    tid = f"{symbol}:{signal_tf}:{last.open_time.isoformat()}"
    win = ms.recommendation.setup
    common = dict(ticket_id=tid, time=last.open_time.isoformat(), symbol=symbol, signal_tf=signal_tf,
                  entry_tf=entry_tf, structural=ms.recommendation.decision,
                  execution=ea.execution, confidence=ea.confidence, reasons=ea.reasons,
                  weakest_factor=ea.weakest_factor, reasoning=REC.reasoning_snapshot(ms, ea))
    if win is None:
        return TradeTicket(action="NO_SETUP", **common)
    risk = abs(win.entry - win.stop)
    action = "TAKE" if ea.execution == "TRADE" else "SKIP"
    return TradeTicket(action=action, entry=win.entry, stop=win.stop, risk=round(risk, 4),
                       exit_target=round(_exit_target(win.direction, win.entry, risk), 4),
                       structural_target=win.target,
                       structural_rr=(round(win.rr, 3) if win.target is not None else None),
                       **common)


def tick_size(symbol: str) -> float:
    inst = C.INSTRUMENTS.get(symbol)
    return inst.tick_size if inst else 0.25
