"""
Frozen model bundle: the exact, versioned artifact every live signal is
traced back to. Fit ONCE on all history up to a training cutoff (live
discipline: train on everything-to-now, predict forward), then frozen.

A bundle carries the fitted classifier, the causal class-return vector
(same math as mnq_system.modeling.evaluate.causal_expected_value, but
estimated once over the whole training set instead of per walk-forward
fold), the cost/tick/point parameters, and full version metadata for
traceability (bundle/model version, training window, code-provenance hash).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from mnq_system.indicators import atr
from mnq_system.modeling.features import DEFAULT_FEATURE_CONFIG, build_feature_matrix
from mnq_system.modeling.labels import build_return_bin_labels, forward_return_atr

BUNDLE_VERSION = "1.1.0"          # bumped when the bundle schema/pipeline changes
MODEL_VERSION = "ev_logreg_h10"   # bumped when the model definition changes
ATR_PERIOD = 14
DEFAULT_HORIZON = 10
DEFAULT_COMMISSION = 1.5
# TWO distinct slippage assumptions, deliberately decoupled:
#  - SIGNAL slippage (1 tick) sets the cost_hurdle that GATES entries -- it
#    reproduces the exact signal calendar the edge was measured on (the
#    research built the calendar at 1 tick; the 3/5-tick numbers were only
#    ever applied to fills when re-costing realized R, never to the entry
#    decision). Using the fill assumption here would raise the entry bar and
#    starve the signal set (~12x fewer signals -- the bug this fixes).
#  - FILL slippage (3 ticks) is the realistic execution assumption applied
#    to expected fills in the shadow book.
DEFAULT_SIGNAL_SLIPPAGE_TICKS = 1.0
DEFAULT_FILL_SLIPPAGE_TICKS = 3.0

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _code_provenance() -> dict:
    """Git commit if this tree is a repo; otherwise an explicit fallback:
    a SHA-256 over the mnq_system source tree, flagged as non-git so the
    provenance is honest rather than a fake hash (this working dir is
    currently NOT a git repo, so the fallback is the expected path)."""
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(_PROJECT_ROOT), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return {"provenance_kind": "git", "commit": commit}
    except (subprocess.CalledProcessError, FileNotFoundError):
        h = hashlib.sha256()
        for p in sorted((_PROJECT_ROOT / "mnq_system").rglob("*.py")):
            h.update(p.read_bytes())
        return {"provenance_kind": "source_tree_sha256", "commit": h.hexdigest()}


@dataclass
class FrozenBundle:
    symbol: str
    horizon: int
    classes: np.ndarray            # sorted unique label values the model predicts
    class_return_vec: np.ndarray   # E[fwd ATR-return | class], aligned to `classes`
    classifier: LogisticRegression
    feature_columns: list
    tick_size: float
    point_value: float
    commission: float
    signal_slippage_ticks: float   # gates entries via cost_hurdle (reproduces research calendar)
    fill_slippage_ticks: float     # realistic execution assumption for shadow-book fills
    round_trip_cost_dollars: float  # computed from SIGNAL slippage (the entry gate)
    meta: dict = field(default_factory=dict)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        # human-readable metadata sidecar (never contains the model itself)
        path.with_suffix(".meta.json").write_text(json.dumps(self.meta, indent=2, default=str))
        return path

    @staticmethod
    def load(path: str | Path) -> "FrozenBundle":
        return joblib.load(path)


def build_bundle(
    bars: pd.DataFrame,
    symbol: str,
    tick_size: float,
    point_value: float,
    account,
    train_end: pd.Timestamp,
    horizon: int = DEFAULT_HORIZON,
    commission: float = DEFAULT_COMMISSION,
    signal_slippage_ticks: float = DEFAULT_SIGNAL_SLIPPAGE_TICKS,
    fill_slippage_ticks: float = DEFAULT_FILL_SLIPPAGE_TICKS,
) -> FrozenBundle:
    """Fit the frozen classifier + class-return vector on all rows with
    entry timestamp <= train_end. Mirrors walk_forward_predict's row
    handling (drop any NaN feature/label row; never impute) and
    causal_expected_value's class-return estimation, collapsed to a single
    train-on-all fit."""
    features = build_feature_matrix({"entry": bars}, account, DEFAULT_FEATURE_CONFIG)
    atr_series = atr(bars, period=ATR_PERIOD)
    labels = build_return_bin_labels(bars, atr_series, horizons=(horizon,))[horizon]
    continuous_return = forward_return_atr(bars["close"], atr_series, horizon)

    train_mask = features.index <= train_end
    valid = features.notna().all(axis=1) & labels.notna() & train_mask
    X = features.loc[valid]
    y = labels.loc[valid].astype(int)
    if y.nunique() < 2:
        raise ValueError(f"{symbol}: <2 label classes in training window; cannot fit.")

    classes = np.sort(y.unique())
    clf = LogisticRegression(max_iter=1000)
    clf.fit(X, y)

    ret = continuous_return.loc[valid]
    class_return_by_class = ret.groupby(y).mean()
    class_return_vec = np.array([class_return_by_class.get(c, 0.0) for c in classes])

    # cost_hurdle / entry gate uses SIGNAL slippage (1 tick) -> reproduces the research calendar
    round_trip_cost_dollars = commission + 2 * signal_slippage_ticks * tick_size * point_value

    meta = {
        "bundle_version": BUNDLE_VERSION,
        "model_version": MODEL_VERSION,
        "symbol": symbol,
        "horizon": horizon,
        "training_start": str(features.index.min()),
        "training_end": str(train_end),
        "n_train_rows": int(len(X)),
        "classes": [int(c) for c in classes],
        "class_return_vec": [float(v) for v in class_return_vec],
        "tick_size": tick_size,
        "point_value": point_value,
        "commission": commission,
        "signal_slippage_ticks": signal_slippage_ticks,
        "fill_slippage_ticks": fill_slippage_ticks,
        "round_trip_cost_dollars": round_trip_cost_dollars,
        "atr_period": ATR_PERIOD,
        "feature_config": repr(DEFAULT_FEATURE_CONFIG),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "databento_dataset": "GLBX.MDP3",
        "databento_bar_schema": "ohlcv-5m",
        **_code_provenance(),
    }

    return FrozenBundle(
        symbol=symbol, horizon=horizon, classes=classes, class_return_vec=class_return_vec,
        classifier=clf, feature_columns=list(X.columns), tick_size=tick_size, point_value=point_value,
        commission=commission, signal_slippage_ticks=signal_slippage_ticks, fill_slippage_ticks=fill_slippage_ticks,
        round_trip_cost_dollars=round_trip_cost_dollars, meta=meta,
    )
