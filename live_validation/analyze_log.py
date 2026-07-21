"""
Reconciliation + Go/No-Go evaluation of a shadow-log JSONL. Reports, per
instrument: realized crossing-slippage distribution (ticks) vs. the
assumption, realized-fill avg R (crossing) vs. expected-fill avg R, and the
pre-registered per-instrument verdict. Prints the overall GO / NO-GO
decision (GO requires >=2 of MYM/M2K/MES to fully pass — replication-first).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, "/Users/gil/trading 2.0")
from mnq_system.backtest.stats import bootstrap_confidence

# Pre-registered thresholds (fixed before any live data — see plan).
MIN_TRADES_VERDICT = 20
SLIPPAGE_MEDIAN_MAX = 3.0      # ticks/side
SLIPPAGE_P90_MAX = 5.0         # ticks/side


def load_trades(path):
    trades = []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("record_type") == "trade":
                trades.append(rec)
    return trades


def per_side_slippage(trades):
    """Both entry and exit slippage-ticks pooled (per-side)."""
    vals = []
    for t in trades:
        for k in ("entry_slippage_ticks", "exit_slippage_ticks"):
            if t.get(k) is not None:
                vals.append(t[k])
    return np.array(vals)


def evaluate_instrument(sym, all_trades):
    # Only contract-consistent observations are reliable execution samples;
    # mismatched-contract trades are excluded entirely (never fabricated).
    trades = [t for t in all_trades if t.get("execution_valid", True)]
    excluded = len(all_trades) - len(trades)
    n = len(trades)
    if n < MIN_TRADES_VERDICT:
        return {"symbol": sym, "n": n, "excluded": excluded, "verdict": "INCONCLUSIVE (n<20)", "pass": False}
    slip = per_side_slippage(trades)
    med, p90 = float(np.median(slip)), float(np.percentile(slip, 90))
    r_cross = [t["r_crossing"] for t in trades if t.get("r_crossing") is not None]
    r_exp = [t["r_expected"] for t in trades if t.get("r_expected") is not None]
    bc = bootstrap_confidence(r_cross)
    be = bootstrap_confidence(r_exp)
    exec_ok = (med <= SLIPPAGE_MEDIAN_MAX) and (p90 <= SLIPPAGE_P90_MAX)
    econ_ok = (bc["mean"] > 0) and (bc["ci_low"] > -abs(bc["mean"]))  # positive, CI not decisively below 0
    passed = exec_ok and econ_ok
    return {
        "symbol": sym, "n": n, "excluded": excluded, "slip_median": med, "slip_p90": p90,
        "r_crossing": bc["mean"], "r_crossing_ci": (bc["ci_low"], bc["ci_high"]),
        "r_expected": be["mean"], "exec_ok": exec_ok, "econ_ok": econ_ok,
        "verdict": "PASS" if passed else "NO-GO", "pass": passed,
    }


def main(path):
    trades = load_trades(path)
    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t["symbol"]].append(t)

    print(f"=== SHADOW-LOG ANALYSIS: {path} ===  total trades={len(trades)}")
    n_pass = 0
    for sym in ["MYM", "M2K", "MES"]:
        r = evaluate_instrument(sym, by_sym.get(sym, []))
        if r.get("pass"):
            n_pass += 1
        exc = f" excluded(contract-mismatch)={r.get('excluded', 0)}" if r.get("excluded") else ""
        if "slip_median" in r:
            print(f"  {sym}: n={r['n']}{exc} slip(median/p90)={r['slip_median']:.2f}/{r['slip_p90']:.2f}t "
                  f"R_crossing={r['r_crossing']:+.4f} CI=[{r['r_crossing_ci'][0]:+.4f},{r['r_crossing_ci'][1]:+.4f}] "
                  f"R_expected={r['r_expected']:+.4f}  exec_ok={r['exec_ok']} econ_ok={r['econ_ok']} -> {r['verdict']}")
        else:
            print(f"  {sym}: n={r['n']}{exc} -> {r['verdict']}")

    decision = "GO" if n_pass >= 2 else ("SINGLE-PASS / NEEDS MORE" if n_pass == 1 else "NO-GO / redirect to model")
    print(f"  ---> instruments passing: {n_pass}/3.  OVERALL: {decision}")
    print("       (GO requires >=2 full passes; 1 = encouraging but insufficient; 0 = redirect to predictive model.)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "shadow_log.jsonl")
