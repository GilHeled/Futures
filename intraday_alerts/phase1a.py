"""
Phase 1a — pre-registered pipeline on MES DEV data only (hold-out untouched;
no MNQ/MYM/M2K). Runs the frozen primary barrier config (+ 3 robustness-only
configs for the Deflated-Sharpe trial count), reports every numerical
Go/No-Go criterion, then STOPS. No Stage-2, no rescue.

Fixed instantiation choices (recorded, not tuned): purged walk-forward =
6 annual folds over 2019–2024, embargo = hold_bars.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from intraday_alerts import config as C
from intraday_alerts.data import annotate_session, load_bars
from intraday_alerts.ev import expected_value, round_trip_cost
from intraday_alerts.features import compute_features
from intraday_alerts.labeling import (AMBIGUOUS, DOWN, TIMEOUT, UP,
                                      label_triple_barrier, session_exit_positions)
from intraday_alerts.purged_cv import purged_walk_forward_splits
from intraday_alerts.topstep import simulate_alert_sequence
from mnq_system.cli import _resolve_contract_spec
from mnq_system.indicators import atr as atr_fn

N_FOLDS = 6
RNG = np.random.default_rng(C.BOOTSTRAP_SEED)


def _fit_predict(Xtr, ytr, Xte):
    """L2 multinomial logistic; C via a small in-fold split; standardized."""
    scaler = StandardScaler().fit(Xtr)
    Xtr_s, Xte_s = scaler.transform(Xtr), scaler.transform(Xte)
    # nested selection: last 20% of train as inner validation, pick C by log-loss
    cut = int(len(Xtr_s) * 0.8)
    best_C, best_ll = C.C_GRID[0], np.inf
    if cut > 10 and len(np.unique(ytr[:cut])) >= 2:
        from sklearn.metrics import log_loss
        for cval in C.C_GRID:
            m = LogisticRegression(C=cval, penalty="l2", class_weight=C.CLASS_WEIGHT, max_iter=1000)
            m.fit(Xtr_s[:cut], ytr[:cut])
            p = m.predict_proba(Xtr_s[cut:])
            try:
                ll = log_loss(ytr[cut:], p, labels=m.classes_)
            except ValueError:
                ll = np.inf
            if ll < best_ll:
                best_ll, best_C = ll, cval
    clf = LogisticRegression(C=best_C, penalty="l2", class_weight=C.CLASS_WEIGHT, max_iter=1000)
    clf.fit(Xtr_s, ytr)
    return clf.predict_proba(Xte_s), clf.classes_


def _proba_col(proba, classes, cls):
    idx = np.where(classes == cls)[0]
    return proba[:, idx[0]] if len(idx) else np.zeros(len(proba))


def run_config(bars, atr, feats, entry_eligible, session_exit_pos, close, high, low,
               k, hold_bars, tick, pv, rt_cost, tret_zero=False, force_side=None, random_side=False):
    """Returns a per-trade DataFrame (entry_ts, et_date, direction, r_multiple, pnl)
    and the realized-trade report."""
    lab = label_triple_barrier(high, low, close, atr.to_numpy(), entry_eligible,
                               session_exit_pos, k=k, hold_bars=hold_bars)
    feat_arr = feats.to_numpy()
    entry_pos = lab["entry_pos"].to_numpy()
    finite = np.isfinite(feat_arr[entry_pos]).all(axis=1)
    lab = lab[finite].reset_index(drop=True)
    entry_pos = lab["entry_pos"].to_numpy()
    labels = lab["label"].to_numpy()
    exit_pos = lab["exit_pos"].to_numpy()
    X = feat_arr[entry_pos]
    et = bars.index.tz_convert(C.TIMEZONE)
    years = np.array([et[p].year for p in entry_pos])

    # training uses only non-ambiguous rows; prediction/backtest can use all
    trainable = labels != AMBIGUOUS
    splits = list(purged_walk_forward_splits(entry_pos, exit_pos, N_FOLDS, embargo_bars=hold_bars))

    p_up = np.full(len(lab), np.nan); p_dn = np.full(len(lab), np.nan); p_to = np.full(len(lab), np.nan)
    tret_hat = np.full(len(lab), np.nan)
    for tr_idx, te_idx in splits:
        tr = tr_idx[trainable[tr_idx]]
        if len(np.unique(labels[tr])) < 2:
            continue
        proba, classes = _fit_predict(X[tr], labels[tr], X[te_idx])
        p_up[te_idx] = _proba_col(proba, classes, UP)
        p_dn[te_idx] = _proba_col(proba, classes, DOWN)
        p_to[te_idx] = _proba_col(proba, classes, TIMEOUT)
        # causal timeout-return estimate from TRAIN TIMEOUT rows only
        to_tr = tr[labels[tr] == TIMEOUT]
        tret_hat[te_idx] = lab["tret"].to_numpy()[to_tr].mean() if len(to_tr) else 0.0

    # build candidate trades from EV decisions on scored (OOS) rows
    candidates = []
    for j in range(len(lab)):
        if np.isnan(p_up[j]):
            continue
        i = int(entry_pos[j]); a = atr.to_numpy()[i]
        if not np.isfinite(a) or a <= 0:
            continue
        ev = expected_value(p_up[j], p_dn[j], p_to[j], k, a, tret_hat[j], pv, rt_cost, tret_zero=tret_zero)
        side = ev.best_side
        if random_side:
            side = "long" if RNG.random() < 0.5 else "short"
        elif force_side is not None:
            side = force_side
        if side is None:
            continue
        entry_price = close[i]
        up = entry_price + k * a; dn = entry_price - k * a
        lb = labels[j]
        if side == "long":
            stop = dn
            exit_price = {UP: up, DOWN: dn, TIMEOUT: close[int(exit_pos[j])], AMBIGUOUS: dn}[lb]
        else:
            stop = up
            exit_price = {DOWN: dn, UP: up, TIMEOUT: close[int(exit_pos[j])], AMBIGUOUS: up}[lb]
        candidates.append({
            "entry_pos": i, "exit_pos": int(exit_pos[j]), "et_date": et[i].date(),
            "direction": side, "entry_price": entry_price, "stop_price": stop,
            "exit_price": exit_price, "risk": k * a * pv, "year": years[j], "entry_ts": bars.index[i],
        })
    realized, report = simulate_alert_sequence(candidates, point_value=pv)
    rows = [{"entry_ts": r["entry_ts"], "et_date": r["et_date"], "year": r["year"],
             "direction": r["direction"], "pnl": r["pnl"], "r_multiple": r["pnl"] / r["risk"]}
            for r in realized]
    return pd.DataFrame(rows), report


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------

def daily_series(trades: pd.DataFrame, value="r_multiple") -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    return trades.groupby("et_date")[value].sum()


def block_bootstrap_mean(daily: np.ndarray, block=C.BOOTSTRAP_BLOCK_DAYS, n=C.BOOTSTRAP_RESAMPLES, seed=C.BOOTSTRAP_SEED):
    d = np.asarray(daily, float); m = len(d)
    if m == 0:
        return {"mean": float("nan"), "p_le_zero": float("nan")}
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(m / block)); maxs = max(m - block, 0)
    starts = rng.integers(0, maxs + 1, size=(n, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, nb * block)[:, :m]
    means = d[idx].mean(axis=1)
    return {"mean": float(d.mean()), "p_le_zero": float((means <= 0).mean())}


def paired_block_diff(a_daily: pd.Series, b_daily: pd.Series, block=C.BOOTSTRAP_BLOCK_DAYS,
                      n=C.BOOTSTRAP_RESAMPLES, seed=C.BOOTSTRAP_SEED):
    idx = a_daily.index.union(b_daily.index)
    diff = (a_daily.reindex(idx, fill_value=0.0) - b_daily.reindex(idx, fill_value=0.0)).to_numpy()
    r = block_bootstrap_mean(diff, block, n, seed)
    return r["mean"], r["p_le_zero"]


def sharpe(daily: np.ndarray):
    d = np.asarray(daily, float)
    return float(d.mean() / d.std(ddof=1) * np.sqrt(252)) if len(d) > 1 and d.std(ddof=1) > 0 else 0.0


def deflated_sharpe_p(daily: np.ndarray, trial_sharpes, n_trials):
    """Probabilistic Sharpe vs a deflated benchmark SR* (Bailey/Lopez de Prado)."""
    d = np.asarray(daily, float)
    T = len(d)
    if T < 3 or d.std(ddof=1) == 0:
        return float("nan")
    from scipy.stats import skew, kurtosis
    sr = d.mean() / d.std(ddof=1)                      # per-day (non-annualized)
    g, kt = skew(d), kurtosis(d, fisher=False)
    var_tr = np.var(trial_sharpes, ddof=1) if len(trial_sharpes) > 1 else (sr * 0.5) ** 2
    e = np.e
    sr_star = np.sqrt(var_tr) * ((1 - np.euler_gamma) * norm.ppf(1 - 1.0 / n_trials)
                                 + np.euler_gamma * norm.ppf(1 - 1.0 / (n_trials * e)))
    num = (sr - sr_star) * np.sqrt(T - 1)
    den = np.sqrt(1 - g * sr + (kt - 1) / 4.0 * sr ** 2)
    return float(1 - norm.cdf(num / den)) if den > 0 else float("nan")


def main():
    spec = _resolve_contract_spec("MES")
    tick, pv = spec.tick_size, spec.point_value
    rt_cost = round_trip_cost(C.COMMISSION_PER_RT, C.SPREAD_TICKS, C.SLIPPAGE_TICKS, tick, pv)

    bars = load_bars("MES", split="dev")             # hold-out untouched
    ann = annotate_session(bars)
    atr = atr_fn(bars, period=C.ATR_PERIOD)
    close, high, low = bars["close"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy()
    entry_eligible = ann["entry_eligible"].to_numpy()
    et_date = np.array([d for d in ann["et_date"].to_numpy()])
    sxp = session_exit_positions(ann["force_flat"].to_numpy(), et_date)
    feats = compute_features(bars, atr)

    print(f"=== PHASE 1a — MES DEV ({bars.index[0].date()}..{bars.index[-1].date()}), n_bars={len(bars)} ===")
    print(f"round-trip cost = ${rt_cost:.2f}; entry-eligible bars = {int(entry_eligible.sum())}")

    def run(k, hold, **kw):
        return run_config(bars, atr, feats, entry_eligible, sxp, close, high, low,
                          k, hold, tick, pv, rt_cost, **kw)

    # primary + robustness (for DSR trial count)
    configs = [("PRIMARY k=1.0/30m", C.PRIMARY_BARRIER)] + \
              [(f"robust k={b['k']}/{b['hold_bars']*5}m", b) for b in C.ROBUSTNESS_BARRIERS]
    trial_sharpes = []
    primary_trades = None
    for name, b in configs:
        tr, rep = run(b["k"], b["hold_bars"])
        dly = daily_series(tr)
        trial_sharpes.append(sharpe(dly.to_numpy()))
        if name.startswith("PRIMARY"):
            primary_trades, primary_report = tr, rep
        print(f"  [{name}] trades={len(tr)} meanR={tr['r_multiple'].mean() if len(tr) else float('nan'):+.4f} "
              f"sharpe={trial_sharpes[-1]:+.2f} prevented_breaches={rep['prevented_breaches']} halts={rep['day_halts']}")

    print("\n--- PRIMARY config Go/No-Go ---")
    tr = primary_trades
    n_days = bars.index.tz_convert(C.TIMEZONE).normalize().nunique()
    dly = daily_series(tr)
    bs = block_bootstrap_mean(dly.to_numpy())
    dsr = deflated_sharpe_p(dly.to_numpy(), trial_sharpes, C.N_BARRIER_TRIALS)
    yrs = tr.groupby("year")["r_multiple"].mean() if len(tr) else pd.Series(dtype=float)
    yrs_pos = int((yrs > 0).sum()); yrs_tot = len(yrs)
    drop_best = yrs.drop(yrs.idxmax()).mean() if yrs_tot > 1 else float("nan")
    alerts_per_day = len(tr) / max(n_days, 1)

    gg = C.GO_NO_GO
    print(f"  trades={len(tr)}  (need >= {gg.min_trades})")
    print(f"  alerts/day={alerts_per_day:.2f}  (need <= {gg.max_alerts_per_day})")
    print(f"  mean R={tr['r_multiple'].mean() if len(tr) else float('nan'):+.4f}  (need >= {gg.min_expectancy_R})")
    print(f"  block-bootstrap P(mean<=0)={bs['p_le_zero']:.3f}  (need <= {gg.max_prob_mean_le_zero})")
    print(f"  Deflated-Sharpe p={dsr:.3f}  (need <= {gg.deflated_sharpe_p_max})")
    print(f"  years positive={yrs_pos}/{yrs_tot}  (need >= {gg.min_years_positive})   drop-best-year meanR={drop_best:+.4f} (need > 0)")
    print(f"  Topstep prevented_breaches={primary_report['prevented_breaches']} day_halts={primary_report['day_halts']} "
          f"final_balance=${primary_report['final_balance']:.0f} mll_locked={primary_report['mll_locked']}")

    # nulls (paired daily block-bootstrap of strategy - null)
    print("\n--- nulls (paired daily block-bootstrap, strategy - null) ---")
    for nm, kw in [("always-long", dict(force_side="long")),
                   ("always-short", dict(force_side="short")),
                   ("random-side", dict(random_side=True))]:
        ntr, _ = run(C.PRIMARY_BARRIER["k"], C.PRIMARY_BARRIER["hold_bars"], **kw)
        md, p = paired_block_diff(dly, daily_series(ntr))
        print(f"  vs {nm}: mean daily diff={md:+.4f}  P(diff<=0)={p:.3f} (need CI>0 ~ P<=0.05)")

    # timeout-term sensitivity (tret forced to 0)
    print("\n--- timeout-term sensitivity (tret=0) ---")
    tr0, _ = run(C.PRIMARY_BARRIER["k"], C.PRIMARY_BARRIER["hold_bars"], tret_zero=True)
    dly0 = daily_series(tr0)
    bs0 = block_bootstrap_mean(dly0.to_numpy())
    prim_mean = tr['r_multiple'].mean() if len(tr) else float("nan")
    zero_mean = tr0['r_multiple'].mean() if len(tr0) else float("nan")
    retained = (zero_mean / prim_mean) if (prim_mean not in (0, float('nan')) and np.isfinite(prim_mean) and prim_mean != 0) else float("nan")
    print(f"  tret=0: trades={len(tr0)} meanR={zero_mean:+.4f} P(mean<=0)={bs0['p_le_zero']:.3f}  "
          f"retained fraction={retained:.2f} (need >=0.50, positive, P<=0.10)")


if __name__ == "__main__":
    main()
