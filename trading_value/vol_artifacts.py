"""
Export the FROZEN causal out-of-sample volatility streams from `market_state`,
aligned to bar timestamps, for the trading study. Reuses the exact frozen
functions (same walk-forward, same fitted model, same HAR baseline) so the values
are identical to the validated Target-A run — the model is never re-fit here.

Produces a DataFrame indexed by bar timestamp with:
  V_forecast : frozen v2 model forward-30min variance forecast (smeared), OOS
  V_har      : HAR baseline forward-30min variance forecast (smeared), OOS

Dev coverage is the walk-forward OOS period (folds 1..5 ⇒ 2020–2024); fold 0
(2019) is training-only and has no OOS forecast, so the trading study's
development window is exactly where both streams exist (matched across arms).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_state import baselines as B
from market_state import config as MS
from market_state import model as MODEL
from market_state.features import FEATURES
from market_state.phase1 import _assemble
from market_state.purged_cv import purged_walk_forward_splits


def build_dev_vol_streams(symbol: str = "MES") -> pd.DataFrame:
    frame = _assemble(symbol)                       # dev only (market_state hold-out guard)
    Xall = frame[list(FEATURES)].values
    sample_pos = np.where(frame["sample_ok"].values)[0]
    entry = frame["pos"].values[sample_pos]
    exit_ = frame["exit_pos"].values[sample_pos]

    idx_parts, vf_parts, vh_parts = [], [], []
    for tr_s, te_s in purged_walk_forward_splits(entry, exit_, MS.N_SPLITS, MS.EMBARGO_BARS):
        full_train_mask = np.zeros(len(frame), dtype=bool)
        full_train_mask[sample_pos[tr_s]] = True
        fc = B.all_forecasts(frame, full_train_mask)          # HAR (smeared) among candidates
        te_full = sample_pos[te_s]
        _, var_pred, _, _, _ = MODEL.fit_predict(
            Xall[sample_pos[tr_s]], frame["log_rv"].values[sample_pos[tr_s]],
            frame["rv"].values[sample_pos[tr_s]], entry[tr_s], exit_[tr_s], Xall[te_full])
        idx_parts.append(frame.index[te_full])
        vf_parts.append(np.asarray(var_pred, dtype=float))
        vh_parts.append(fc["har"]["var"].values[te_full].astype(float))

    idx = idx_parts[0].append(idx_parts[1:]) if len(idx_parts) > 1 else idx_parts[0]
    out = pd.DataFrame(
        {"V_forecast": np.concatenate(vf_parts), "V_har": np.concatenate(vh_parts)},
        index=idx,
    ).sort_index()
    out = out[(out["V_forecast"] > 0) & (out["V_har"] > 0)]
    return out
