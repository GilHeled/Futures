"""
Step 1a.5 — Reconciliation. Explain quantitatively why the current code path's
overnight trade count/edge differs from the original validated research.

Compares, per instrument:
  - the SAVED frozen bundle (bundles/<sym>.joblib, built 2026-07-20) vs a FRESH
    rebuild with the CURRENT code, on the IDENTICAL training window;
  - version / provenance / feature-config / model-coefficient differences;
  - the exact OOS window and cache coverage;
  - sequential shadow-book trade count + avg R@3t under each bundle;
  - the full off-hours signal-calendar size (the matched-entry universe);
  - a trade-by-trade divergence set where the two bundles disagree.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from live_validation import execution_sensitivity as ES
from live_validation.bundle import DEFAULT_HORIZON, FrozenBundle, build_bundle, _code_provenance
from live_validation.inference import compute_frame
from mnq_system.cli import _resolve_contract_spec
from mnq_system.config import DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import build_provider

SYMBOLS = ("MYM", "M2K", "MES")
OOS_START = pd.Timestamp("2023-01-01", tz="UTC")


def _trades(frame, tick, pv, sym_start):
    tr = [t for t in ES._raw_trades(frame) if t["entry_ts"] >= sym_start]
    r = np.array([ES._net_r(t, tick, pv, 3) for t in tr])
    r = r[np.isfinite(r)]
    return tr, (float(r.mean()) if len(r) else float("nan"))


def _universe(frame, sym_start):
    d = frame["direction"].values
    off = frame["off_hours"].values
    se = frame["session_ending"].values
    idx = frame.index
    return int(np.sum(np.isfinite(d) & (d != 0) & off & (~se) & (idx >= sym_start)))


def run():
    provider = build_provider("databento", cache=True)
    end = pd.Timestamp("2026-07-09", tz="UTC")
    cur_prov = _code_provenance()
    lines = []
    lines.append(f"CURRENT mnq_system code provenance: {cur_prov['provenance_kind']} = {cur_prov['commit'][:16]}…")
    for sym in SYMBOLS:
        acct = replace(DEFAULT_ACCOUNT_CONFIG, contract=_resolve_contract_spec(sym))
        tick, pv = acct.contract.tick_size, acct.contract.point_value
        saved = FrozenBundle.load(Path("bundles") / f"{sym}.joblib")
        sm = saved.meta
        te = pd.Timestamp(sm["training_end"])
        full = provider.get_historical_bars(sym, pd.Timestamp("2019-05-01", tz="UTC").to_pydatetime(),
                                            end.to_pydatetime(), "5m")
        fresh = build_bundle(full.loc[:te], sym, tick, pv, acct, te, horizon=DEFAULT_HORIZON, commission=1.5)

        # --- metadata / provenance / model diffs ---
        prov_match = sm.get("commit") == cur_prov.get("commit")
        feat_match = sm.get("feature_config") == fresh.meta.get("feature_config")
        cols_match = list(saved.feature_columns) == list(fresh.feature_columns)
        crv_saved = np.asarray(saved.class_return_vec, float)
        crv_fresh = np.asarray(fresh.class_return_vec, float)
        crv_match = crv_saved.shape == crv_fresh.shape and np.allclose(crv_saved, crv_fresh)
        coef_match = (saved.classifier.coef_.shape == fresh.classifier.coef_.shape
                      and np.allclose(saved.classifier.coef_, fresh.classifier.coef_))

        # --- OOS trade counts under each bundle (current inference code both times) ---
        oos = full[full.index >= pd.Timestamp("2022-10-01", tz="UTC")]
        f_saved = compute_frame(oos, saved, acct).join(oos[["open", "high", "low", "close"]])
        f_fresh = compute_frame(oos, fresh, acct).join(oos[["open", "high", "low", "close"]])
        tr_saved, r_saved = _trades(f_saved, tick, pv, OOS_START)
        tr_fresh, r_fresh = _trades(f_fresh, tick, pv, OOS_START)
        uni_saved = _universe(f_saved, OOS_START)
        uni_fresh = _universe(f_fresh, OOS_START)

        es_saved = set(str(t["entry_ts"]) for t in tr_saved)
        es_fresh = set(str(t["entry_ts"]) for t in tr_fresh)
        only_saved = sorted(es_saved - es_fresh)
        only_fresh = sorted(es_fresh - es_saved)

        lines += [
            f"\n===== {sym} =====",
            f"  saved bundle: version={sm.get('bundle_version')}/{sm.get('model_version')} "
            f"train={sm.get('training_start','?')[:10]}..{str(sm.get('training_end'))[:10]} "
            f"n_train={sm.get('n_train_rows')} provenance={sm.get('provenance_kind')}:{str(sm.get('commit'))[:12]}",
            f"  fresh  bundle: n_train={fresh.meta.get('n_train_rows')} provenance={cur_prov['provenance_kind']}:{cur_prov['commit'][:12]}",
            f"  MATCHES?  code_provenance={prov_match}  feature_config={feat_match}  "
            f"feature_cols={cols_match}  class_return_vec={crv_match}  classifier_coef={coef_match}",
            f"  OOS window: {OOS_START.date()} .. {end.date()}   cache: {full.index.min()} .. {full.index.max()}",
            f"  off-hours signal UNIVERSE (full calendar): saved={uni_saved}  fresh={uni_fresh}",
            f"  sequential shadow-book trades: saved n={len(tr_saved)} avgR@3t={r_saved:+.4f} | "
            f"fresh n={len(tr_fresh)} avgR@3t={r_fresh:+.4f}",
            f"  trade-set divergence: only-in-saved={len(only_saved)}  only-in-fresh={len(only_fresh)}",
        ]
        if only_saved[:3] or only_fresh[:3]:
            lines.append(f"    e.g. only-saved: {only_saved[:3]}")
            lines.append(f"    e.g. only-fresh: {only_fresh[:3]}")
    return "\n".join(lines)


if __name__ == "__main__":
    out = run()
    print(out)
    p = Path("live_validation/results"); p.mkdir(parents=True, exist_ok=True)
    (p / "reconciliation_raw.txt").write_text(out + "\n")
