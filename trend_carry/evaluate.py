"""Phase 1 (trend) Go/No-Go evaluation — frozen protocol §6, §8.

Single pass: DEV (2010-2019, descriptive) + OOS (2020-2024, the gate). The
locked hold-out (2025-01-01..2026-07-09) is evaluated ONLY by
`confirm_holdout()`, run manually and only if OOS passes.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from trend_carry import config as C
from trend_carry import continuous as K
from trend_carry import data as D
from trend_carry import signals as G
from trend_carry import stats as ST
from trend_carry.backtest import run_backtest

RESULTS = pathlib.Path(__file__).resolve().parent / "results"


def _build():
    rolls = K.build_rolls()
    returns = K.build_returns(rolls)
    adjusted = K.build_adjusted(returns)
    front = K.build_front_close(rolls)
    signal = G.trend_signal(adjusted)
    prov = {"n_contracts": sum(int(r["front_iid"].nunique()) for r in rolls.values()),
            "start": returns.index.min(), "end": returns.index.max(),
            "ndays": int(len(returns))}
    return returns, adjusted, front, signal, prov


def _window_metrics(res, returns, window, label):
    net = res.window(*window).net
    return {
        "label": label, "sharpe": ST.ann_sharpe(net),
        "boot_p": ST.block_bootstrap_p(net), "psr": ST.probabilistic_sharpe(net),
        "maxdd": ST.max_drawdown(net), "mean": float(net.mean()),
        "ndays": int(net.size), "net": net,
    }


def evaluate(write: bool = True) -> dict:
    returns, adjusted, front, signal, prov = _build()
    roots = list(C.ROOTS)
    oos = (C.OOS_START, C.OOS_END)
    dev = (C.DEV_START, C.DEV_END)

    res1 = run_backtest(returns, signal, front, roots, cost_mult=1.0)
    res2 = run_backtest(returns, signal, front, roots, cost_mult=C.COST_STRESS_MULT)

    dev_m = _window_metrics(res1, returns, dev, "DEV 2010-2019 (1x)")
    oos_m = _window_metrics(res1, returns, oos, "OOS 2020-2024 (1x)")
    oos2_m = _window_metrics(res2, returns, oos, f"OOS 2020-2024 ({C.COST_STRESS_MULT}x)")

    # benchmarks on OOS
    oos_net = res1.window(*oos).net
    beta_sr, beta_net = ST.passive_beta_sharpe(returns, front, oos, 1.0, signal)
    null = ST.random_sign_null(returns, front, roots, oos, 1.0, signal)
    null_pctile = float((null < oos_m["sharpe"]).mean())  # fraction of null we beat
    ba = ST.beta_alpha(oos_net, returns["ES"].reindex(oos_net.index))

    # breadth on OOS
    oos_inst = res1.window(*oos).net_inst
    sec_sr = ST.sector_sharpes(oos_inst)
    db_sec_sr, best_sec = ST.drop_best_sector_sharpe(oos_inst)
    yr_sr = ST.yearly_sharpes(oos_net)
    db_yr_sr, best_yr = ST.drop_best_year_sharpe(oos_net)

    # Go/No-Go criteria
    crit = {}
    crit["sharpe>=0.5"] = oos_m["sharpe"] >= C.SHARPE_MIN
    crit["bootstrap P<=0.05"] = oos_m["boot_p"] <= C.BOOTSTRAP_P_MAX
    crit["PSR/DSR>0.5"] = oos_m["psr"] > 0.5
    crit["beats passive beta"] = oos_m["sharpe"] > beta_sr
    crit["beats random null (>95%)"] = null_pctile >= 0.95
    crit["alpha>0 vs beta"] = ba["alpha"] > 0
    crit["majority sectors +"] = sum(v > 0 for v in sec_sr.values()) > len(sec_sr) / 2
    crit["drop-best-sector +"] = db_sec_sr > 0
    crit["majority years +"] = sum(v > 0 for v in yr_sr.values()) > len(yr_sr) / 2
    crit["drop-best-year +"] = db_yr_sr > 0
    crit["2x-cost still +"] = oos2_m["mean"] > 0
    crit["2x-cost significant"] = oos2_m["boot_p"] <= C.BOOTSTRAP_P_MAX
    overall = all(crit.values())

    report = _format(dev_m, oos_m, oos2_m, beta_sr, null, null_pctile, ba,
                     sec_sr, db_sec_sr, best_sec, yr_sr, db_yr_sr, best_yr,
                     crit, overall, prov)
    print(report)
    if write:
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "phase1_trend_dev_oos.txt").write_text(report + "\n")
    return {"crit": crit, "overall": overall, "oos": oos_m, "dev": dev_m,
            "oos_2x": oos2_m, "report": report}


def _format(dev_m, oos_m, oos2_m, beta_sr, null, null_pctile, ba, sec_sr,
            db_sec_sr, best_sec, yr_sr, db_yr_sr, best_yr, crit, overall, prov) -> str:
    L = ["=" * 78,
         "PHASE 1 — TREND — DEV/OOS GO-NO-GO (frozen pre-registration)",
         "canonical TSMOM ensemble {21,63,126,252}d, 14 roots, net of cost",
         "=" * 78,
         f"contracts={prov['n_contracts']}  days={prov['ndays']}  "
         f"range={prov['start'].date()}..{prov['end'].date()}",
         "", "-- window summary --"]
    for m in (dev_m, oos_m, oos2_m):
        L.append(f"  {m['label']:26s} Sharpe={m['sharpe']:+.3f}  "
                 f"bootP={m['boot_p']:.4f}  PSR={m['psr']:.3f}  "
                 f"maxDD={m['maxdd']:+.1%}  n={m['ndays']}")
    L += ["", "-- OOS benchmarks --",
          f"  passive equity beta Sharpe = {beta_sr:+.3f}   (book must beat)",
          f"  random-sign null Sharpe: mean={null.mean():+.3f} p95={np.percentile(null,95):+.3f}"
          f"  -> book beats {null_pctile:.1%} of null",
          f"  beta-fit vs ES: alpha/day={ba['alpha']:+.2e}  beta={ba['beta']:+.2f}"
          f"  corr={ba['corr']:+.2f}  R2={ba['r2']:.2f}",
          "", "-- OOS breadth: sector Sharpes --"]
    for s, v in sec_sr.items():
        L.append(f"    {s:9s} {v:+.3f}")
    L.append(f"  drop-best-sector ({best_sec}) Sharpe = {db_sec_sr:+.3f}")
    L.append("-- OOS breadth: yearly Sharpes --")
    L.append("    " + "  ".join(f"{y}:{v:+.2f}" for y, v in yr_sr.items()))
    L.append(f"  drop-best-year ({best_yr}) Sharpe = {db_yr_sr:+.3f}")
    L += ["", "-- GO/NO-GO criteria --"]
    for k, v in crit.items():
        L.append(f"  [{'PASS' if v else 'FAIL'}] {k}")
    L += ["", f"OVERALL: {'GO' if overall else 'NO-GO'}",
          "", "Locked hold-out 2025-01-01..2026-07-09: NOT TOUCHED"
          + (" (eligible for one confirmation)" if overall else " (remains locked; OOS did not pass)"),
          "=" * 78]
    return "\n".join(L)


def confirm_holdout() -> dict:
    """ONE-TIME hold-out confirmation. Call only after a GO on OOS."""
    returns, adjusted, front, signal, prov = _build()
    res = run_backtest(returns, signal, front, list(C.ROOTS), cost_mult=1.0)
    net = res.window(C.HOLDOUT_START, C.HOLDOUT_END).net
    out = {"sharpe": ST.ann_sharpe(net), "boot_p": ST.block_bootstrap_p(net),
           "mean": float(net.mean()), "maxdd": ST.max_drawdown(net), "ndays": int(net.size)}
    confirmed = out["mean"] > 0 and out["boot_p"] <= C.BOOTSTRAP_P_MAX and out["sharpe"] > 0
    out["confirmed"] = confirmed
    print(f"HOLD-OUT: Sharpe={out['sharpe']:+.3f} bootP={out['boot_p']:.4f} "
          f"maxDD={out['maxdd']:+.1%} n={out['ndays']} -> "
          f"{'CONFIRMED' if confirmed else 'NOT CONFIRMED'}")
    return out


if __name__ == "__main__":
    evaluate()
