"""
Phase A final step — CAUSAL RE-MEASUREMENT of the overnight strategy.

The look-ahead features (`gap_atr_ratio`, `overnight_imbalance_pctile`) encode the
FUTURE 09:30-ET session-open event and cannot be made causal in the overnight
window; the strictly-causal model therefore EXCLUDES them and is retrained from
scratch on the remaining 9 causal features. Every overnight bar is still scored —
now on causal information only. We re-run the exact sequential ShadowBook and
compare the leaked model vs the causal model, per instrument, net of cost.

Definitive: if the edge survives on causal features it is real (new strategy of
record); if it collapses, the overnight edge was a look-ahead artifact and the
project closes.
"""
from __future__ import annotations

import pathlib
from dataclasses import replace

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from live_validation import execution_sensitivity as ES
from live_validation.bundle import ATR_PERIOD, DEFAULT_HORIZON, FrozenBundle
from live_validation.inference import compute_frame
from mnq_system.cli import _resolve_contract_spec
from mnq_system.config import DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import build_provider
from mnq_system.indicators import atr
from mnq_system.modeling.features import DEFAULT_FEATURE_CONFIG, build_feature_matrix
from mnq_system.modeling.labels import build_return_bin_labels, forward_return_atr

LEAKY = ["gap_atr_ratio", "overnight_imbalance_pctile"]
TE = pd.Timestamp("2022-12-31", tz="UTC")
OOS = pd.Timestamp("2023-01-01", tz="UTC")


def build_causal_bundle(bars, sym, tick, pv, account, train_end, horizon=DEFAULT_HORIZON, commission=1.5):
    feats = build_feature_matrix({"entry": bars}, account, DEFAULT_FEATURE_CONFIG).drop(columns=LEAKY)
    assert not (set(LEAKY) & set(feats.columns)), "leaky features not excluded!"
    atr_series = atr(bars, period=ATR_PERIOD)
    labels = build_return_bin_labels(bars, atr_series, horizons=(horizon,))[horizon]
    cont = forward_return_atr(bars["close"], atr_series, horizon)
    valid = feats.notna().all(axis=1) & labels.notna() & (feats.index <= train_end)
    X, y = feats.loc[valid], labels.loc[valid].astype(int)
    classes = np.sort(y.unique())
    clf = LogisticRegression(max_iter=1000).fit(X, y)
    crb = cont.loc[valid].groupby(y).mean()
    crv = np.array([crb.get(c, 0.0) for c in classes])
    return FrozenBundle(
        symbol=sym, horizon=horizon, classes=classes, class_return_vec=crv, classifier=clf,
        feature_columns=list(X.columns), tick_size=tick, point_value=pv, commission=commission,
        signal_slippage_ticks=1.0, fill_slippage_ticks=3.0,
        round_trip_cost_dollars=commission + 2 * 1.0 * tick * pv,
        meta={"causal_fix": "dropped " + ",".join(LEAKY), "training_end": str(train_end)})


def _measure(frame, tick, pv):
    trades = [t for t in ES._raw_trades(frame) if t["entry_ts"] >= OOS]
    r3 = np.array([ES._net_r(t, tick, pv, 3) for t in trades]); r3 = r3[np.isfinite(r3)]
    r5 = np.array([ES._net_r(t, tick, pv, 5) for t in trades]); r5 = r5[np.isfinite(r5)]
    years = {}
    for t in trades:
        yr = pd.Timestamp(t["entry_ts"]).year
        years.setdefault(yr, []).append(ES._net_r(t, tick, pv, 3))
    per_year = {yr: float(np.nanmean(v)) for yr, v in sorted(years.items())}
    return len(r3), float(r3.mean()), float(r5.mean()), per_year


def run():
    prov = build_provider("databento", cache=True)
    end = pd.Timestamp("2026-07-09", tz="UTC")
    L = ["=" * 78, "OVERNIGHT STRATEGY — CAUSAL RE-MEASUREMENT (leaked model vs causal model)",
         "sequential ShadowBook, frozen train 2022-12-31, OOS 2023-2026, net of cost", "=" * 78]
    from live_validation.bundle import build_bundle
    for sym in ("MYM", "M2K", "MES"):
        acct = replace(DEFAULT_ACCOUNT_CONFIG, contract=_resolve_contract_spec(sym))
        tick, pv = acct.contract.tick_size, acct.contract.point_value
        full = prov.get_historical_bars(sym, pd.Timestamp("2019-05-01", tz="UTC").to_pydatetime(),
                                        end.to_pydatetime(), "5m")
        oos = full[full.index >= pd.Timestamp("2022-10-01", tz="UTC")]
        leaked = build_bundle(full.loc[:TE], sym, tick, pv, acct, TE, horizon=DEFAULT_HORIZON, commission=1.5)
        causal = build_causal_bundle(full.loc[:TE], sym, tick, pv, acct, TE)
        fl = compute_frame(oos, leaked, acct).join(oos[["open", "high", "low", "close"]])
        fc = compute_frame(oos, causal, acct).join(oos[["open", "high", "low", "close"]])
        nl, l3, l5, ly = _measure(fl, tick, pv)
        nc, c3, c5, cy = _measure(fc, tick, pv)
        L.append(f"\n{sym}")
        L.append(f"  LEAKED (11 feat): n={nl:4d}  avgR@3t={l3:+.3f}  @5t={l5:+.3f}  "
                 f"per-year@3t={ {y: round(v,2) for y,v in ly.items()} }")
        L.append(f"  CAUSAL ( 9 feat): n={nc:4d}  avgR@3t={c3:+.3f}  @5t={c5:+.3f}  "
                 f"per-year@3t={ {y: round(v,2) for y,v in cy.items()} }")
    L.append("\n" + "=" * 78)
    return "\n".join(L)


if __name__ == "__main__":
    out = run()
    print(out)
    p = pathlib.Path("live_validation/results"); p.mkdir(parents=True, exist_ok=True)
    (p / "causal_remeasurement.txt").write_text(out + "\n")
