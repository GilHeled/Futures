"""TradeRecord — one flat, minimal record per engine recommendation, for measurement.

Fields (exactly the agreed minimal set):
  AT ENTRY   scene_id, timestamp, symbol, engine_direction, execution(TRADE/PASS), entry, stop,
             target, risk (points), reward_R, and a reasoning snapshot
             {manipulation, mss, fvg, dealing_range, execution_score, weakest_factor}
  DURING     triggered, hit_stop, hit_tp, mfe_R, mae_R
  AFTER      result_R, win, note

TP model is v1: one entry / one stop / one target / full exit — but `tp_mode` is stored so later
variants (partials, breakeven, manual) can be added WITHOUT changing the schema. Nothing here fits
or optimizes anything; it just assembles engine output + a market outcome into a comparable row.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from ict_live.engine import execution_quality as EQ

TP_MODE_V1 = "single_target_full_exit"


@dataclass(frozen=True)
class TradeRecord:
    # --- at entry ---
    scene_id: str
    timestamp: str
    symbol: str
    engine_direction: str            # LONG / SHORT
    execution: str                   # TRADE / PASS (from execution v1)
    entry: float
    stop: float
    target: Optional[float]
    risk: float                      # |entry - stop| in points (= 1R)
    reward_R: Optional[float]        # target distance in R
    reasoning: dict                  # {manipulation, mss, fvg, dealing_range, execution_score, weakest_factor}
    tp_mode: str = TP_MODE_V1
    # --- during ---
    triggered: Optional[bool] = None
    hit_stop: Optional[bool] = None
    hit_tp: Optional[bool] = None
    mfe_R: Optional[float] = None
    mae_R: Optional[float] = None
    # --- after ---
    result_R: Optional[float] = None
    win: Optional[bool] = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _fmt(x, nd=2):
    return None if x is None else round(x, nd)


def reasoning_snapshot(ms, ea) -> dict:
    """Public: compact reasoning snapshot for a MarketState + execution assessment (shared by the
    trade record and the live trade ticket)."""
    return _reasoning_snapshot(ms, ea)


def _reasoning_snapshot(ms, ea) -> dict:
    """Compact reasoning snapshot of the current winning setup's chain (strings; for review)."""
    win = ms.recommendation.setup
    snap = {"manipulation": None, "mss": None, "fvg": None, "dealing_range": None,
            "execution_score": ea.confidence, "weakest_factor": ea.weakest_factor}
    if ms.ranges:
        dr = ms.ranges[0]
        snap["dealing_range"] = f"{dr.source_tf} {_fmt(dr.low)}-{_fmt(dr.high)} ({dr.direction}) CE {_fmt(dr.ce)}"
    if win is None:
        return snap
    swp = EQ._by_dep(ms.ranked_sweeps, win.depends_on, "SWP")
    mss = EQ._by_dep(ms.ranked_mss, win.depends_on, "MSS")
    fvg = EQ._by_dep(ms.ranked_fvgs, win.depends_on, "FVG")
    if swp is None and fvg is not None:
        disp = EQ._by_dep(ms.ranked_displacements, fvg.depends_on, "DISP")
        swp = EQ._by_dep(ms.ranked_sweeps, disp.depends_on, "SWP") if disp else None
    if swp is not None:
        snap["manipulation"] = f"{swp.direction} sweep of {_fmt(swp.pool_price)}"
    if mss is not None:
        snap["mss"] = f"{mss.state} {mss.direction} @ {_fmt(mss.broken_price)}"
    if fvg is not None:
        snap["fvg"] = f"{fvg.direction} {fvg.status} CE {_fmt(fvg.ce)} [{fvg.tf}]"
    return snap


def _from_outcome(outcome: Optional[dict]) -> dict:
    """Map an outcomes.label_setup payload onto the during/after fields."""
    if not outcome or outcome.get("status") != "labelled":
        return {}
    triggered = outcome.get("fill_index") is not None
    course = outcome.get("course_execution", {})
    result = course.get("result")
    exc = outcome.get("excursion", {})
    rR = course.get("realized_R")
    return {"triggered": triggered,
            "hit_stop": (result == "STOP") if triggered else False,
            "hit_tp": (result == "TARGET") if triggered else False,
            "mfe_R": exc.get("mfe_R"), "mae_R": exc.get("mae_R"),
            "result_R": rR,
            "win": (rR is not None and rR > 0) if triggered else None}


def build(ms, *, scene_id: str, symbol: str, timestamp: str,
          outcome: Optional[dict] = None, note: str = "") -> Optional[TradeRecord]:
    """Assemble a TradeRecord from a MarketState (recommendation + execution v1) and an optional
    market outcome. Returns None when there is no setup to record."""
    win = ms.recommendation.setup
    if win is None:
        return None
    ea = EQ.assess(ms)
    return TradeRecord(
        scene_id=scene_id, timestamp=timestamp, symbol=symbol,
        engine_direction=("LONG" if win.direction == "long" else "SHORT"),
        execution=ea.execution, entry=win.entry, stop=win.stop, target=win.target,
        risk=round(win.risk, 4), reward_R=(round(win.rr, 3) if win.target is not None else None),
        reasoning=_reasoning_snapshot(ms, ea), note=note,
        **_from_outcome(outcome))
