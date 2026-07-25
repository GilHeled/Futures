"""
Pre-registered §13 diagnostic (dev-only, NON-gating), run because Study 1 is a NULL.
Distinguishes two explanations:
  (a) the arms are nearly identical (forecast ≈ HAR) ⇒ the +2.3% edge is TOO SMALL
      TO MONETIZE via this channel;
  (b) the arms differ materially but Sharpe does not improve ⇒ the null is SPECIFIC
      to dynamic stop/target adaptation.
Reports: correlation of forecast vs HAR D(t); fraction of trades whose exit differs
between the forecast and naive arms; distribution of per-trade PnL differences.
No methodological change; the hold-out is not touched.
"""
from __future__ import annotations

import pathlib

import numpy as np

from trading_value import channel, config as C
from trading_value import strategies as S
from trading_value import vol_sources
from trading_value.study1 import _load_dev_bars
from trading_value.vol_artifacts import build_dev_vol_streams


def run() -> str:
    bars = _load_dev_bars()
    streams = build_dev_vol_streams(C.INSTRUMENT)
    D, _ = vol_sources.build_range_distances(bars, streams)
    ok = D["entry_ok"].values

    # (i) similarity of the two range streams on eligible bars
    m = ok & np.isfinite(D["forecast"].values) & np.isfinite(D["naive"].values)
    d_corr = float(np.corrcoef(D["forecast"].values[m], D["naive"].values[m])[0, 1])
    v_corr = float(np.corrcoef(np.sqrt(streams["V_forecast"]), np.sqrt(streams["V_har"]))[0, 1])
    rel_gap = float(np.mean(np.abs(D["forecast"].values[m] - D["naive"].values[m])
                            / D["naive"].values[m]))

    # (ii)/(iii) per-matched-trade exit differences and PnL differences
    diff_frac, pnl_diffs = [], []
    for strat in C.STRATEGIES:
        sig = S.compute_signals(bars, strat)
        for cfg, k, mm in C.KM_CONFIGS:
            f = channel.simulate(bars, sig, D["forecast"], ok, k, mm)
            na = channel.simulate(bars, sig, D["naive"], ok, k, mm)
            n = min(len(f), len(na))
            fe = f["exit_fill"].values[:n]; ne = na["exit_fill"].values[:n]
            fr = f["reason"].values[:n]; nr = na["reason"].values[:n]
            differ = (np.abs(fe - ne) > 1e-9) | (fr != nr)
            diff_frac.append(differ.mean())
            pnl_diffs.append((f["pnl_usd"].values[:n] - na["pnl_usd"].values[:n]))
    pnl_diffs = np.concatenate(pnl_diffs)
    frac = float(np.mean(diff_frac))

    L = ["=" * 74, "STUDY 1 — §13 DIAGNOSTIC (why the null?), MES dev", "=" * 74,
         f"corr( forecast D(t) , HAR D(t) ) on eligible bars = {d_corr:.4f}",
         f"corr( sqrt V_forecast , sqrt V_HAR )              = {v_corr:.4f}",
         f"mean |forecast−HAR| / HAR   (relative gap)        = {rel_gap*100:.2f}%",
         f"fraction of matched trades whose EXIT differs      = {frac*100:.2f}%",
         f"per-trade PnL diff (forecast−naive): mean={pnl_diffs.mean():+.3f}$  "
         f"std={pnl_diffs.std():.3f}$  p5={np.percentile(pnl_diffs,5):+.2f}  "
         f"p95={np.percentile(pnl_diffs,95):+.2f}",
         "-" * 74]
    tstat = float(pnl_diffs.mean() / (pnl_diffs.std(ddof=1) / np.sqrt(len(pnl_diffs))))
    L.append(f"per-trade PnL diff t-stat (illustrative) = {tstat:.2f}  (n={len(pnl_diffs)})")
    L.append("-" * 74)
    if frac < 0.15 and d_corr > 0.95:
        L.append("INTERPRETATION: arms are nearly identical; the forecast barely moves stop/target")
        L.append("outcomes at all -- 'too small to monetize' via this channel.")
    elif abs(tstat) < 2:
        L.append(f"INTERPRETATION: the forecast changes the exit on {frac*100:.0f}% of trades (arms are NOT")
        L.append("identical), but the per-trade differences are DIRECTIONLESS NOISE (mean approx 0 vs large")
        L.append("std, t approx 0). The forecast reshuffles which trades win/lose without a SYSTEMATIC edge")
        L.append("over HAR: its small (+2.3% QLIKE) informational advantage does not translate into economic")
        L.append("value through stop/target adaptation.")
    else:
        L.append("INTERPRETATION: arms differ AND the per-trade difference is systematically signed, yet")
        L.append("daily Sharpe does not improve -- a channel-specific effect worth further study.")
    L.append("Also: the base strategies are themselves unprofitable (all arms negative Sharpe), so there")
    L.append("is little positive edge for the forecast to modulate. The hold-out remains LOCKED.")
    L.append("=" * 74)
    return "\n".join(L)


if __name__ == "__main__":
    rep = run()
    print(rep)
    out = pathlib.Path("trading_value/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "study1_diagnostic.txt").write_text(rep + "\n")
