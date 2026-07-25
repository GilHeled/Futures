"""
Study 1 POST-MORTEM (dev-only, exploratory, NON-gating). Decomposes the matched
per-trade PnL difference (forecast − HAR) by market regime to see whether the null
is uniform (→ forecast economically redundant with HAR) or concentrated in specific
conditions (→ a *narrowly* focused, separately pre-registered Study 2 might be
justified — never a claim on its own; this is hypothesis-GENERATING).

Conditioning variables (all at entry): forecast–HAR disagreement magnitude;
forecast-vol tercile; realized-vol tercile; trend-vs-range day; opening hour vs rest.
Does NOT touch the hold-out and changes no frozen study choice.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from market_state.phase1 import _assemble
from trading_value import channel, config as C
from trading_value import strategies as S
from trading_value import vol_sources
from trading_value.study1 import _load_dev_bars
from trading_value.vol_artifacts import build_dev_vol_streams


def _day_efficiency_ratio(bars: pd.DataFrame) -> pd.Series:
    def er(x):
        d = np.abs(np.diff(x.values)).sum()
        return abs(x.values[-1] - x.values[0]) / d if d > 0 else 0.0
    return bars.groupby("et_date")["close"].apply(er)


def _collect() -> pd.DataFrame:
    bars = _load_dev_bars()
    streams = build_dev_vol_streams(C.INSTRUMENT)
    D, _ = vol_sources.build_range_distances(bars, streams)
    ok = D["entry_ok"].values
    disagree = (D["forecast"] - D["naive"]).abs() / D["naive"]
    rv_real = _assemble(C.INSTRUMENT)["rv"].reindex(bars.index)
    er_day = _day_efficiency_ratio(bars)

    parts = []
    for strat in C.STRATEGIES:
        sig = S.compute_signals(bars, strat)
        for cfg, k, m in C.KM_CONFIGS:
            f = channel.simulate(bars, sig, D["forecast"], ok, k, m)
            na = channel.simulate(bars, sig, D["naive"], ok, k, m)
            n = min(len(f), len(na))
            ets = pd.DatetimeIndex(f["entry_ts"].values[:n]).tz_localize("UTC")
            parts.append(pd.DataFrame({
                "pnl_diff": f["pnl_usd"].values[:n] - na["pnl_usd"].values[:n],
                "entry_ts": ets,
                "et_date": f["entry_date"].values[:n],
                "disagree": disagree.reindex(ets).values,
                "v_fc": D["forecast"].reindex(ets).values,
                "rv_real": rv_real.reindex(ets).values,
                "er_day": pd.Series(f["entry_date"].values[:n]).map(er_day).values,
                "hour_open": np.array([t.tz_convert(C.TIMEZONE).hour == 10 for t in ets]),
            }))
    return pd.concat(parts, ignore_index=True)


def _tstat(x):
    x = x[np.isfinite(x)]
    sd = x.std(ddof=1)
    return 0.0 if sd == 0 or len(x) < 2 else x.mean() / (sd / np.sqrt(len(x)))


def _tercile_rows(df, col, labels=("low", "mid", "high")):
    v = df[col].values
    finite = np.isfinite(v)
    q1, q2 = np.quantile(v[finite], [1 / 3, 2 / 3])
    out = []
    for lab, lo, hi in [(labels[0], -np.inf, q1), (labels[1], q1, q2), (labels[2], q2, np.inf)]:
        m = finite & (v > lo) & (v <= hi)
        pv = df["pnl_diff"].values[m]
        out.append((lab, int(m.sum()), float(np.nanmean(pv)), _tstat(pv)))
    return out


def run() -> str:
    df = _collect()
    L = ["=" * 78, "STUDY 1 — REGIME POST-MORTEM (dev-only, exploratory), MES",
         "matched per-trade PnL difference (forecast − HAR), $ ; t = mean/(sd/√n)",
         "=" * 78,
         f"pooled n={len(df)}  overall mean={df['pnl_diff'].mean():+.3f}$  t={_tstat(df['pnl_diff'].values):.2f}",
         ""]

    def block(title, rows):
        L.append(f"--- {title} ---")
        for lab, n, mean, t in rows:
            L.append(f"   {lab:>6}: n={n:5d}  mean={mean:+7.3f}$  t={t:+.2f}")
        L.append("")

    block("by forecast–HAR disagreement magnitude (tercile)", _tercile_rows(df, "disagree"))
    block("by forecast-volatility level (tercile)", _tercile_rows(df, "v_fc"))
    block("by realized-volatility level (tercile)", _tercile_rows(df, "rv_real"))

    med = np.nanmedian(df["er_day"].values)
    trend = df[df["er_day"] > med]["pnl_diff"].values
    rng = df[df["er_day"] <= med]["pnl_diff"].values
    L.append("--- trend vs range day (efficiency-ratio median split) ---")
    L.append(f"    trend: n={len(trend):5d}  mean={np.nanmean(trend):+7.3f}$  t={_tstat(trend):+.2f}")
    L.append(f"    range: n={len(rng):5d}  mean={np.nanmean(rng):+7.3f}$  t={_tstat(rng):+.2f}")
    L.append("")
    op = df[df["hour_open"]]["pnl_diff"].values
    rest = df[~df["hour_open"]]["pnl_diff"].values
    L.append("--- opening hour (10:00–11:00 ET) vs rest of session ---")
    if len(op) == 0:
        L.append("    open: n=0 — the forecast is UNAVAILABLE before ~11:30 ET (market_state 24-bar")
        L.append("          feature warmup), so no eligible trades exist in the opening hour. Dimension vacuous.")
    else:
        L.append(f"    open: n={len(op):5d}  mean={np.nanmean(op):+7.3f}$  t={_tstat(op):+.2f}")
    L.append(f"    rest: n={len(rest):5d}  mean={np.nanmean(rest):+7.3f}$  t={_tstat(rest):+.2f}")
    L.append("")

    # honest multiple-testing guard: ~14 buckets tested
    all_t = []
    for c in ("disagree", "v_fc", "rv_real"):
        all_t += [t for *_, t in _tercile_rows(df, c)]
    all_t += [_tstat(trend), _tstat(rng), _tstat(op), _tstat(rest)]
    max_abs_t = max(abs(t) for t in all_t)
    L.append("-" * 78)
    L.append(f"largest |t| across the ~{len(all_t)} regime buckets = {max_abs_t:.2f}")
    L.append("(Bonferroni note: ~|t|>=3.0 needed for 0.05 family-wise over this many buckets.)")
    if max_abs_t < 3.0:
        L.append("VERDICT: the forecast−HAR PnL difference is centered on ZERO in EVERY regime tested.")
        L.append("No condition shows a systematic edge. Evidence that the forecast is economically")
        L.append("REDUNDANT with HAR for stop management is now strong. No Study 2 is justified on this")
        L.append("basis; closing the trading-value line would be the disciplined conclusion.")
    else:
        L.append("VERDICT: at least one regime shows a bucket effect surviving the rough multiple-testing")
        L.append("bar. This is HYPOTHESIS-GENERATING ONLY — it would require a NEW, narrowly-scoped,")
        L.append("separately pre-registered study to confirm; it is not evidence on its own.")
    L.append("The hold-out remains LOCKED.")
    L.append("=" * 78)
    return "\n".join(L)


if __name__ == "__main__":
    rep = run()
    print(rep)
    out = pathlib.Path("trading_value/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "study1_postmortem.txt").write_text(rep + "\n")
