"""
Study 1 runner (MES DEV only): dynamic vol-adaptive stop / fixed target.
Evaluates the 6 pre-registered configs × 3 vol sources and reports against the
frozen Go/No-Go (§11). Hold-out is never touched here.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, skew

from market_state.data import annotate_session, load_bars
from trading_value import channel, config as C, metrics as M
from trading_value import strategies as S
from trading_value import vol_sources
from trading_value.vol_artifacts import build_dev_vol_streams


def _load_dev_bars():
    bars = annotate_session(load_bars(C.INSTRUMENT, split="dev"))
    return bars[bars["in_rth"]].copy().sort_index()


def run() -> dict:
    bars = _load_dev_bars()
    streams = build_dev_vol_streams(C.INSTRUMENT)
    D, c_source = vol_sources.build_range_distances(bars, streams)
    entry_ok = D["entry_ok"].values
    all_dates = bars["et_date"].values

    daily = {}          # (strategy, cfg, source) -> daily PnL Series
    trades = {}
    for strat in C.STRATEGIES:
        sig = S.compute_signals(bars, strat)
        for cfg, k, m in C.KM_CONFIGS:
            for src in C.VOL_SOURCES:
                tr = channel.simulate(bars, sig, D[src], entry_ok, k, m)
                trades[(strat, cfg, src)] = tr
                daily[(strat, cfg, src)] = channel.daily_pnl(tr, all_dates)

    rows = []
    fc_daily_sharpes = []
    for si, strat in enumerate(C.STRATEGIES):
        for ci, (cfg, k, m) in enumerate(C.KM_CONFIGS):
            f, na, no = (daily[(strat, cfg, s)] for s in ("forecast", "naive", "none"))
            boot = M.paired_block_bootstrap_dsharpe(
                f.values, na.values, seed=C.BOOTSTRAP_SEED + si * 3 + ci)
            diff = pd.Series(f.values - na.values, index=f.index)
            dd_f, dd_n = M.max_drawdown(f.values), M.max_drawdown(na.values)
            rows.append({
                "strategy": strat, "config": cfg, "k": k, "m": m,
                "sharpe_none": M.ann_sharpe(no.values),
                "sharpe_naive": M.ann_sharpe(na.values),
                "sharpe_forecast": M.ann_sharpe(f.values),
                "dsharpe_vs_naive": M.ann_sharpe(f.values) - M.ann_sharpe(na.values),
                "boot_p": boot["p_le_zero"], "boot_ci": (boot["ci_lo"], boot["ci_hi"]),
                "pnl_forecast": M.total_pnl(f.values),
                "n_trades_fc": int(len(trades[(strat, cfg, "forecast")])),
                "maxdd_forecast": dd_f, "maxdd_naive": dd_n,
                "dd_ok": dd_f <= dd_n * (1 + C.DD_TOLERANCE),
                "drop_best_month": M.drop_best_period_mean(diff, "M"),
                "drop_best_year": M.drop_best_period_mean(diff, "Y"),
                "diff": diff,
            })
            fc_daily_sharpes.append(M.daily_sharpe(f.values))

    # DSR across the 6 forecast-arm configs (guard on absolute Sharpe)
    best_i = int(np.argmax([r["sharpe_forecast"] for r in rows]))
    bser = daily[(rows[best_i]["strategy"], rows[best_i]["config"], "forecast")].values
    dsr = M.deflated_sharpe(fc_daily_sharpes, T=len(bser),
                            skew=float(skew(bser)), kurt=float(kurtosis(bser, fisher=True) + 3))

    # qualifying config = primary corrected significance + all secondary gates
    qualifiers = []
    for r in rows:
        primary = (r["dsharpe_vs_naive"] > 0) and (r["boot_p"] <= C.ALPHA_CORRECTED)
        concentration = (r["drop_best_month"] > 0) and (r["drop_best_year"] > 0)
        if primary and r["pnl_forecast"] > 0 and r["dd_ok"] and concentration:
            qualifiers.append(r)
    dev_pass = len(qualifiers) > 0

    return {"rows": rows, "dsr": dsr, "qualifiers": qualifiers, "dev_pass": dev_pass,
            "c_source": c_source, "n_days": len(set(all_dates))}


def format_report(res: dict) -> str:
    L = ["=" * 78,
         "STUDY 1 (DEV) — Volatility-Forecast Stop/Target Adaptation, MES",
         "Primary: net daily Sharpe improvement forecast vs HAR; corrected across 6 configs",
         "=" * 78,
         f"normalization c_source (dev-only): "
         f"none={res['c_source']['none']:.4f} naive=1.0000 forecast={res['c_source']['forecast']:.4f}",
         f"per-config corrected significance level = {C.ALPHA_CORRECTED:.4f} (0.05/6)",
         ""]
    L.append(f"{'strat':<10}{'cfg':<4}{'Sh_none':>9}{'Sh_naive':>9}{'Sh_fcst':>9}"
             f"{'dShrp':>8}{'boot_p':>8}{'PnL_fc$':>10}{'nTr':>6}{'DDok':>5}{'qual':>5}")
    for r in res["rows"]:
        qual = "YES" if ((r["dsharpe_vs_naive"] > 0) and (r["boot_p"] <= C.ALPHA_CORRECTED)
                         and r["pnl_forecast"] > 0 and r["dd_ok"]
                         and r["drop_best_month"] > 0 and r["drop_best_year"] > 0) else ""
        L.append(f"{r['strategy']:<10}{r['config']:<4}{r['sharpe_none']:>9.3f}"
                 f"{r['sharpe_naive']:>9.3f}{r['sharpe_forecast']:>9.3f}"
                 f"{r['dsharpe_vs_naive']:>8.3f}{r['boot_p']:>8.4f}"
                 f"{r['pnl_forecast']:>10.0f}{r['n_trades_fc']:>6}"
                 f"{('Y' if r['dd_ok'] else 'N'):>5}{qual:>5}")
    L.append("")
    L.append("--- concentration check (qualifying candidates only) ---")
    for r in res["rows"]:
        if (r["dsharpe_vs_naive"] > 0) and (r["boot_p"] <= C.ALPHA_CORRECTED):
            L.append(f"  {r['strategy']}/{r['config']}: drop-best-month mean={r['drop_best_month']:+.1f}$  "
                     f"drop-best-year mean={r['drop_best_year']:+.1f}$")
    L.append("")
    L.append(f"--- DSR (forecast arm, best of 6): dsr={res['dsr']['dsr']:.4f}  "
             f"sr_hat(daily)={res['dsr']['sr_hat_daily']:.4f}  sr*={res['dsr']['sr_star_daily']:.4f} ---")
    L.append("")
    L.append("--- supporting (NOT gates) ---")
    n_fc_gt_naive = sum(r["dsharpe_vs_naive"] > 0 for r in res["rows"])
    n_fc_gt_none = sum(r["sharpe_forecast"] > r["sharpe_none"] for r in res["rows"])
    L.append(f"  configs with Sharpe_forecast > Sharpe_naive: {n_fc_gt_naive}/6")
    L.append(f"  configs with Sharpe_forecast > Sharpe_none : {n_fc_gt_none}/6")
    L.append(f"  mean dSharpe (forecast−naive) across 6      : "
             f"{np.mean([r['dsharpe_vs_naive'] for r in res['rows']]):+.3f}")
    L.append("")
    L.append("--- DEV GATE (§11) ---")
    L.append(f"  qualifying configs (all conditions): {len(res['qualifiers'])}")
    for q in res["qualifiers"]:
        L.append(f"    PASS: {q['strategy']}/{q['config']} "
                 f"dSharpe={q['dsharpe_vs_naive']:+.3f} p={q['boot_p']:.4f} PnL=${q['pnl_forecast']:.0f}")
    L.append("")
    L.append(f"RESULT: {'DEV GATE PASSED — hold-out confirmation authorized' if res['dev_pass'] else 'NO-GO (dev gate not passed) — hold-out stays locked'}")
    L.append("=" * 78)
    return "\n".join(L)


if __name__ == "__main__":
    res = run()
    report = format_report(res)
    print(report)
    out = pathlib.Path("trading_value/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "study1_dev.txt").write_text(report + "\n")
