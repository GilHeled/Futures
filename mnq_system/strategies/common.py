"""
Shared building blocks reused across multiple Strategy implementations --
NOT strategy-specific, unlike each strategies/<name>/ package. Keep this
file limited to genuinely duplicated logic; a single caller doesn't belong
here.
"""

from __future__ import annotations

from mnq_system.candlesticks import Bar
from mnq_system.strategy_api import ExitDecision, Position


def noop_add_cli_arguments(parser) -> None:
    """Shared no-op for STRATEGY_REGISTRY entries that don't own the
    add_cli_arguments call for a set of flags they nonetheless read (e.g.
    several benchmark_*/hypothesis specs sharing one set of CLI flags) --
    every registered spec's add_cli_arguments runs against the same shared
    parser, and argparse errors if the same flag is added twice.
    """
    return


def simple_stop_target_exit(position: Position, bar: Bar) -> ExitDecision:
    """Stop-hit > target-hit > none, fill at the trigger level itself (the
    same backtest simplification used throughout this codebase). Shared by
    every Strategy whose exit is nothing more than a fixed stop and a
    single fixed target -- ORBStrategy, NaiveBenchmarkStrategy, and every
    HypothesisStrategy subclass.
    """
    is_long = position.direction == "long"

    stop_hit = bar.low <= position.stop_price if is_long else bar.high >= position.stop_price
    if stop_hit:
        return ExitDecision(action="stop", fill_price=position.stop_price, fraction=1.0)

    target_hit = bar.high >= position.target_1 if is_long else bar.low <= position.target_1
    if target_hit:
        return ExitDecision(action="full_target", fill_price=position.target_1, fraction=1.0)

    return ExitDecision(action="none")
