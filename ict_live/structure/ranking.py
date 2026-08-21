"""Transparent candidate RANKING engine (EXPERIMENTAL; nothing frozen).

Per B3 and the methodology owner: the context layer RANKS candidates, never filters them. Every
objectively-valid candidate is retained with an explicit, explainable priority — no learned score,
no hidden weights.

The ranker is DOMAIN-AGNOSTIC: it knows nothing about ICT. It executes an ordered list of
independent FACTOR EVALUATORS and produces the lexicographic ordering. Each evaluator maps an item
to a `FactorValue(name, value, explanation)` (higher value = stronger). The same engine ranks
sweeps, displacement legs, MSS candidates, FVGs, and setups — only the evaluator list changes.

Every ranking exposes a COMPLETE pairwise comparison: each candidate carries `lost_to_prev`, the
factor-by-factor reason it ranked below the candidate immediately above it (equal factors listed,
then the first differing factor that decides — the essence of lexicographic order).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

Evaluator = Callable[[Any], "FactorValue"]


@dataclass(frozen=True)
class FactorValue:
    name: str
    value: float              # comparable; higher = stronger
    explanation: str = ""     # what this value means for THIS candidate


@dataclass(frozen=True)
class Ranked:
    item: Any
    rank: int                 # 1 = top priority
    tied: bool                # shares its full factor key with another candidate (ambiguity)
    factors: tuple            # tuple[FactorValue, ...] in priority order
    key: tuple                # the compared lexicographic key (the factor values)
    lost_to_prev: str = ""    # why this lost to the candidate ranked immediately above ("" for #1)


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _key(fvs) -> tuple:
    return tuple(f.value for f in fvs)


def _pairwise(winner: tuple, loser: tuple) -> str:
    """Factor-by-factor: list equal leading factors, then the first differing factor that decides."""
    parts = []
    for w, l in zip(winner, loser):
        if l.value == w.value:
            parts.append(f"{w.name} equal ({_fmt(w.value)})")
        else:
            parts.append(f"{w.name}: {_fmt(l.value)} < {_fmt(w.value)} ← decides")
            return "; ".join(parts)
    parts.append("all factors equal (tie — order not decided by any factor)")
    return "; ".join(parts)


def rank(items: list, evaluators: list[Evaluator]) -> list[Ranked]:
    evaluated = [(it, tuple(ev(it) for ev in evaluators)) for it in items]
    evaluated.sort(key=lambda x: _key(x[1]), reverse=True)
    keys = [_key(fvs) for _, fvs in evaluated]
    out = []
    for i, (it, fvs) in enumerate(evaluated):
        tied = keys.count(_key(fvs)) > 1
        lost = _pairwise(evaluated[i - 1][1], fvs) if i > 0 else ""
        out.append(Ranked(it, i + 1, tied, fvs, _key(fvs), lost))
    return out


def factor_names(evaluators: list[Evaluator], probe: Any) -> list[str]:
    """Names in priority order (evaluate against a sample item)."""
    return [ev(probe).name for ev in evaluators]
