"""TradeTracker — the stateful trade lifecycle. Opens a position from a TAKE ticket and resolves it
against subsequent closed signal-TF bars using the FROZEN exit model (full exit at +EXIT_TARGET_R,
stop at the manipulation extreme = −1R). Records every trade with EXPECTED (ex-ante) vs ACTUAL.

Resolution mirrors the backtest exactly (so live and study are apples-to-apples): fill = first bar
whose range contains the entry; then TARGET (+2R) / STOP (−1R) / HORIZON (mark-to-close). If a single
bar spans both the stop and the target, it is flagged ambiguous and resolved conservatively to the
stop (−1R). No trading logic beyond the frozen exit; append-only records via engine.annotations-style
JSONL is left to the reporting layer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from ict_live.engine import execution_quality as EQ

DEFAULT_HORIZON = 96                        # signal-TF bars to hold before a horizon mark


@dataclass
class OpenTrade:
    ticket_id: str
    symbol: str
    signal_tf: str
    direction: str                          # long / short
    entry: float
    stop: float
    exit_target: float                      # +EXIT_TARGET_R
    risk: float
    opened_time: str
    structural_target: Optional[float] = None
    reasoning: dict = field(default_factory=dict)
    status: str = "PENDING"                 # PENDING (awaiting fill) -> OPEN -> CLOSED
    fill_time: Optional[str] = None
    bars_seen: int = 0
    bars_since_fill: int = 0
    mfe_R: float = 0.0                       # measurement only (excursion); not a decision input
    mae_R: float = 0.0
    horizon: int = DEFAULT_HORIZON


@dataclass(frozen=True)
class ClosedTrade:
    ticket_id: str
    symbol: str
    signal_tf: str
    direction: str
    entry: float
    stop: float
    exit_target: float
    risk: float
    opened_time: str
    # actual
    filled: bool
    fill_time: Optional[str]
    result: str                             # TARGET / STOP / HORIZON / AMBIGUOUS / NO_FILL
    result_R: Optional[float]
    win: Optional[bool]
    bars_held: Optional[int]
    close_time: Optional[str]
    # expected (ex-ante)
    expected_R: float                       # the mechanical target in R (+EXIT_TARGET_R)
    mfe_R: float = 0.0                       # max favorable / adverse excursion (measurement only)
    mae_R: float = 0.0
    structural_target: Optional[float] = None
    reasoning: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _hit_stop(b, stop, long):
    return b.low <= stop if long else b.high >= stop


def _hit(b, level, long):
    return b.high >= level if long else b.low <= level


class TradeTracker:
    """Feed closed signal-TF bars via `update(bar)`; returns any trades that CLOSED on that bar."""

    def __init__(self, *, horizon: int = DEFAULT_HORIZON):
        self.open: dict[str, OpenTrade] = {}
        self.closed: list[ClosedTrade] = []
        self.horizon = horizon

    def open_from_ticket(self, ticket) -> Optional[OpenTrade]:
        """Open a PENDING trade from a TAKE ticket (idempotent per ticket_id). Ignores non-TAKE."""
        if ticket.action != "TAKE" or ticket.ticket_id in self.open:
            return None
        ot = OpenTrade(ticket_id=ticket.ticket_id, symbol=ticket.symbol, signal_tf=ticket.signal_tf,
                       direction=("long" if ticket.structural == "LONG" else "short"),
                       entry=ticket.entry, stop=ticket.stop, exit_target=ticket.exit_target,
                       risk=ticket.risk, opened_time=ticket.time,
                       structural_target=ticket.structural_target, reasoning=dict(ticket.reasoning),
                       horizon=self.horizon)
        self.open[ticket.ticket_id] = ot
        return ot

    def update(self, bar) -> list[ClosedTrade]:
        """Advance every open trade for this bar's symbol by one closed bar; return closures."""
        closed_now = []
        for tid, ot in list(self.open.items()):
            if ot.symbol != getattr(bar, "symbol", ot.symbol) and hasattr(bar, "symbol"):
                # Bar has no symbol field in this codebase; tracker is fed per-symbol streams.
                pass
            ot.bars_seen += 1
            long = ot.direction == "long"
            just_filled = False
            if ot.status == "PENDING":
                if bar.low <= ot.entry <= bar.high:
                    ot.status, ot.fill_time = "OPEN", bar.open_time.isoformat()
                    ot.bars_since_fill, just_filled = 0, True
                else:
                    if ot.bars_seen >= ot.horizon:              # never filled within horizon
                        closed_now.append(self._close(ot, "NO_FILL", None, None, bar))
                    continue
            if ot.status == "OPEN":
                if not just_filled:                            # the fill bar itself is bar 0
                    ot.bars_since_fill += 1
                fav = ((bar.high - ot.entry) if long else (ot.entry - bar.low)) / ot.risk
                adv = ((ot.entry - bar.low) if long else (bar.high - ot.entry)) / ot.risk
                ot.mfe_R = max(ot.mfe_R, fav)                  # measurement only — no decision effect
                ot.mae_R = max(ot.mae_R, adv)
                s, t = _hit_stop(bar, ot.stop, long), _hit(bar, ot.exit_target, long)
                if s and t:
                    closed_now.append(self._close(ot, "AMBIGUOUS", -1.0, ot.bars_since_fill, bar))
                elif t:
                    closed_now.append(self._close(ot, "TARGET", EQ.EXIT_TARGET_R, ot.bars_since_fill, bar))
                elif s:
                    closed_now.append(self._close(ot, "STOP", -1.0, ot.bars_since_fill, bar))
                elif ot.bars_since_fill >= ot.horizon:
                    mark = ((bar.close - ot.entry) if long else (ot.entry - bar.close)) / ot.risk
                    closed_now.append(self._close(ot, "HORIZON", round(mark, 3), ot.bars_since_fill, bar))
        return closed_now

    def _close(self, ot: OpenTrade, result: str, result_R, bars_held, bar) -> ClosedTrade:
        del self.open[ot.ticket_id]
        filled = result != "NO_FILL"
        ct = ClosedTrade(
            ticket_id=ot.ticket_id, symbol=ot.symbol, signal_tf=ot.signal_tf, direction=ot.direction,
            entry=ot.entry, stop=ot.stop, exit_target=ot.exit_target, risk=ot.risk,
            opened_time=ot.opened_time, filled=filled, fill_time=ot.fill_time, result=result,
            result_R=result_R, win=(result_R is not None and result_R > 0) if filled else None,
            bars_held=bars_held, close_time=bar.open_time.isoformat(),
            expected_R=EQ.EXIT_TARGET_R, mfe_R=round(ot.mfe_R, 3), mae_R=round(ot.mae_R, 3),
            structural_target=ot.structural_target, reasoning=ot.reasoning)
        self.closed.append(ct)
        return ct

    def snapshot(self) -> dict:
        return {"open": [asdict(o) for o in self.open.values()], "closed": len(self.closed)}
