"""Aggregate trade records into the measurements that matter — expectancy (avg R), win rate, and
the key A/B: do execution-v1 TRADE recommendations have better expectancy than the ones v1 would
PASS? Purely descriptive counting; no fitting.

Expectancy is computed over FILLED records with a defined result_R (NO_FILL and AMBIGUOUS_INTRABAR
are counted but excluded from the R mean, since their R is undefined)."""
from __future__ import annotations

from typing import Iterable


def _agg(records: list) -> dict:
    n = len(records)
    filled = [r for r in records if r.get("triggered")]
    scored = [r for r in filled if r.get("result_R") is not None]     # defined R
    wins = [r for r in scored if r.get("result_R", 0) > 0]
    rs = [r["result_R"] for r in scored]
    exp = round(sum(rs) / len(rs), 3) if rs else None
    return {"n": n, "filled": len(filled), "scored": len(scored),
            "no_fill": sum(1 for r in records if r.get("triggered") is False),
            "ambiguous": sum(1 for r in filled if r.get("result_R") is None),
            "win_rate": round(len(wins) / len(scored), 3) if scored else None,
            "expectancy_R": exp,
            "total_R": round(sum(rs), 2) if rs else None,
            "avg_mfe_R": round(sum(r["mfe_R"] for r in filled if r.get("mfe_R") is not None)
                               / len(filled), 2) if filled else None,
            "avg_mae_R": round(sum(r["mae_R"] for r in filled if r.get("mae_R") is not None)
                               / len(filled), 2) if filled else None}


def _by(records: list, key) -> dict:
    groups: dict = {}
    for r in records:
        groups.setdefault(key(r), []).append(r)
    return {str(k): _agg(v) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}


def summarize(records: Iterable) -> dict:
    """records: an iterable of TradeRecord or dict rows."""
    rows = [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in records]
    trade = [r for r in rows if r.get("execution") == "TRADE"]
    passed = [r for r in rows if r.get("execution") == "PASS"]
    out = {
        "overall": _agg(rows),
        "by_execution": {"TRADE": _agg(trade), "PASS": _agg(passed)},
        "by_direction": _by(rows, lambda r: r.get("engine_direction")),
        "by_symbol": _by(rows, lambda r: r.get("symbol")),
        "by_weakest_factor": _by(rows, lambda r: r.get("reasoning", {}).get("weakest_factor")),
    }
    # headline A/B: does the v1 execution filter select better trades?
    te, pe = out["by_execution"]["TRADE"]["expectancy_R"], out["by_execution"]["PASS"]["expectancy_R"]
    out["v1_filter_edge_R"] = (round(te - pe, 3) if te is not None and pe is not None else None)
    return out


def render_md(s: dict, title: str = "Trade-record statistics") -> str:
    def row(name, a):
        return (f"| {name} | {a['n']} | {a['filled']} | {a['win_rate']} | {a['expectancy_R']} | "
                f"{a['total_R']} | {a['avg_mfe_R']} | {a['avg_mae_R']} |")
    L = [f"# {title}", "",
         "| group | n | filled | win rate | expectancy R | total R | avg MFE | avg MAE |",
         "|---|---|---|---|---|---|---|---|",
         row("ALL", s["overall"]),
         row("v1 TRADE", s["by_execution"]["TRADE"]),
         row("v1 PASS", s["by_execution"]["PASS"])]
    L += ["", f"**v1 filter edge (TRADE − PASS expectancy): {s['v1_filter_edge_R']} R**", "",
          "## By direction", "",
          "| dir | n | filled | win rate | expectancy R | total R |", "|---|---|---|---|---|---|"]
    for k, a in s["by_direction"].items():
        L.append(f"| {k} | {a['n']} | {a['filled']} | {a['win_rate']} | {a['expectancy_R']} | {a['total_R']} |")
    L += ["", "## By symbol", "", "| sym | n | filled | win rate | expectancy R | total R |",
          "|---|---|---|---|---|---|"]
    for k, a in s["by_symbol"].items():
        L.append(f"| {k} | {a['n']} | {a['filled']} | {a['win_rate']} | {a['expectancy_R']} | {a['total_R']} |")
    return "\n".join(L) + "\n"
