"""Deterministic, stable ids for engine objects so every derived object can name its
dependencies (the `depends_on` chain the audit traces back to raw structure).

Ids are pure functions of the object's causal identity (bar index / kind / TF), so the same
market prefix always yields the same ids — no counters, no ordering surprises, replay-stable.
"""
from __future__ import annotations


def bar_id(index: int) -> str:
    return f"BAR{index}"


def swing_id(swing) -> str:
    return f"SW{swing.index}{'H' if swing.kind == 'high' else 'L'}"


def pool_id(pool) -> str:
    return f"ERL{pool.index}{'H' if pool.kind == 'high' else 'L'}"


def dr_id(dr) -> str:
    return f"DR-{dr.source_tf}"


def sweep_id(pool_index: int, kind: str, bar_index: int) -> str:
    """Stable per swept-pool + sweeping bar."""
    return f"SWP{pool_index}{'H' if kind == 'high' else 'L'}@{bar_index}"


def displacement_id(start_index: int, end_index: int, direction: str) -> str:
    return f"DISP{start_index}-{end_index}{'D' if direction == 'bearish' else 'U'}"


def mss_id(disp_start: int, broken_index: int, direction: str) -> str:
    return f"MSS{disp_start}x{broken_index}{'D' if direction == 'bearish' else 'U'}"


def fvg_id(i: int, direction: str) -> str:
    return f"FVG{i}{'D' if direction == 'bearish' else 'U'}"


def setup_id(fvg_ident: str) -> str:
    return f"SETUP:{fvg_ident}"
