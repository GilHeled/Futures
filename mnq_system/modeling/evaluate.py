"""
Walk-forward evaluation of a market-state predictive model.

Unlike the trade-based walk-forward (mnq_system.backtest.stats's
equal_time_windows -- fixed-size, independent windows), a predictive
model's training set should only ever grow forward in time and never see
the future relative to what it's tested on: expanding_folds() enforces
that.

Every out-of-sample prediction is compared against
sklearn.dummy.DummyClassifier(strategy="prior") -- the ML analogue of the
benchmark_* naive strategies (mnq_system/strategies/benchmarks/): it
predicts only the training fold's own empirical class frequencies, with
zero feature information, and is the "chance" baseline every result is
judged against.

`walk_forward_predict` is the single canonical implementation of "retrain
on an expanding window, predict out-of-sample, never revisit" -- both
`evaluate_horizon` (research-time metrics) and
`mnq_system.strategies.model_driven` (a live `Strategy` trading on those
same predictions) call it, so there is exactly one place this causal
guarantee is implemented and tested.

Log-loss and (multiclass) Brier score are both means, over rows, of a
per-row statistic -- kept as per-row arrays so their bootstrap CIs reuse
mnq_system.backtest.stats.bootstrap_confidence directly, rather than a new
bootstrap implementation.

Beyond the headline log-loss/Brier improvement, this module reports
several things a single aggregate number can hide (the same "don't trust
the aggregate alone" discipline as the trade-based walk-forward/fragility
checks elsewhere in this codebase):
- calibration error per predicted class (does "70%" actually happen ~70%
  of the time?),
- coefficient stability across folds (is a feature's relationship with the
  outcome consistent over time, or does it flip sign fold to fold?),
- a regime-conditional breakdown (by ATR-regime tercile and by calendar
  year) of the same improvement statistic, so a result driven by one
  volatility regime or one year isn't mistaken for a persistent one,
- a confidence-decile analysis: "Market state -> better-than-random
  prediction" (what the log-loss/Brier/calibration numbers above establish)
  is a different claim from "higher model confidence -> better decisions"
  (what a trading rule built on these predictions would actually need).
  Ranking every out-of-sample row by the model's own top-1 predicted
  probability and checking whether accuracy and realized directional
  return both rise monotonically into the top confidence decile answers
  that second question directly, before any EV estimation or trading rule
  is built on top of it. A directional-call-only variant additionally
  guards against a short-horizon artifact: when the model's top-1
  prediction is "flat" (direction-neutral) for nearly every row regardless
  of confidence, the all-rows signed-return metric measures near-zero
  noise diluted by that non-directional majority, not real ranking among
  the calls that would actually be traded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression

from mnq_system.backtest.stats import bootstrap_confidence

DEFAULT_N_FOLDS = 8
DEFAULT_MIN_TRAIN_FRACTION = 0.2
DEFAULT_CALIBRATION_BUCKETS = 10
DEFAULT_CONFIDENCE_DECILES = 10
REGIME_COLUMN = "atr_regime_pctile"  # present on the real feature matrix; breakdown skipped if absent


def expanding_folds(n: int, n_folds: int, min_train_fraction: float = DEFAULT_MIN_TRAIN_FRACTION) -> list:
    """`n_folds` non-overlapping, chronological test windows over `n` rows,
    each trained on everything strictly before it. The first
    `min_train_fraction` of the data is reserved as a minimum training set
    before the first test fold begins -- an untested model has no business
    being evaluated on the very first bars of history. Returns a list of
    (train_slice, test_slice) index-position slices.
    """
    start_pos = int(n * min_train_fraction)
    test_positions = np.linspace(start_pos, n, n_folds + 1, dtype=int)
    folds = []
    for i in range(n_folds):
        test_lo, test_hi = int(test_positions[i]), int(test_positions[i + 1])
        if test_hi <= test_lo:
            continue
        folds.append((slice(0, test_lo), slice(test_lo, test_hi)))
    return folds


def _align_proba(model, X: pd.DataFrame, classes: np.ndarray) -> np.ndarray:
    """`model.predict_proba`'s columns are ordered by `model.classes_`,
    which may omit a class entirely absent from its training fold --
    remap to a fixed column order (`classes`) so every fold's predictions
    are directly comparable/concatenable.
    """
    raw = model.predict_proba(X)
    aligned = np.zeros((len(X), len(classes)), dtype=float)
    for i, c in enumerate(model.classes_):
        j = int(np.searchsorted(classes, c))
        aligned[:, j] = raw[:, i]
    return aligned


def _align_coef(model, classes: np.ndarray, feature_names: list) -> pd.DataFrame:
    """Same remap as `_align_proba`, for `model.coef_` -- rows for a class
    missing from this fold's training data are NaN rather than absent, so
    per-feature stability can be computed across folds with a consistent
    shape.

    sklearn's `LogisticRegression.coef_` has shape (n_classes, n_features)
    for 3+ classes, but only (1, n_features) for exactly 2 -- a single
    decision boundary, implicitly `coef_[0]` for `classes_[1]` and its
    negation for `classes_[0]` (since P(class0) = 1 - P(class1) under a
    single sigmoid). Handle that binary special case explicitly.
    """
    aligned = pd.DataFrame(np.nan, index=[f"class_{c}" for c in classes], columns=feature_names)
    if model.coef_.shape[0] == 1 and len(model.classes_) == 2:
        aligned.loc[f"class_{model.classes_[0]}"] = -model.coef_[0]
        aligned.loc[f"class_{model.classes_[1]}"] = model.coef_[0]
    else:
        for i, c in enumerate(model.classes_):
            aligned.loc[f"class_{c}"] = model.coef_[i]
    return aligned


@dataclass
class WalkForwardPredictions:
    classes: np.ndarray
    proba: pd.DataFrame  # index = features.index (full, incl. pre-fold rows); columns = classes; model's OOS proba
    baseline_proba: pd.DataFrame  # same shape; sklearn.dummy.DummyClassifier(strategy="prior")'s OOS proba
    fold_coefs: list  # one _align_coef(...) DataFrame per fold actually fitted
    fold_meta: list  # one dict per fold actually fitted: {"fold", "test_index", "train_index", "n_train", "n_test"}


def walk_forward_predict(
    features: pd.DataFrame,
    labels: pd.Series,
    n_folds: int = DEFAULT_N_FOLDS,
    min_train_fraction: float = DEFAULT_MIN_TRAIN_FRACTION,
) -> WalkForwardPredictions:
    """Fits a fresh `LogisticRegression` (and a `DummyClassifier(strategy="prior")`
    baseline) on each expanding fold's training slice, predicts only that
    fold's held-out test slice, and never revisits it -- the single
    causal, no-lookahead retrain-and-predict loop every consumer of a
    walk-forward-validated model in this codebase shares.

    Every row of `features`/`labels` with any NaN feature or a NaN label is
    excluded entirely (never imputed). Rows before the first fold's test
    window begins (the reserved `min_train_fraction` prefix) get NaN
    probabilities -- no prediction exists yet, and never will after the
    fact.
    """
    valid = features.notna().all(axis=1) & labels.notna()
    X_valid = features.loc[valid]
    y_valid = labels.loc[valid].astype(int)
    index_valid = X_valid.index
    X = X_valid.reset_index(drop=True)
    y = y_valid.reset_index(drop=True)

    classes = np.sort(y.unique())
    folds = expanding_folds(len(X), n_folds, min_train_fraction)

    proba_full = np.full((len(X), len(classes)), np.nan)
    baseline_proba_full = np.full((len(X), len(classes)), np.nan)
    fold_coefs, fold_meta = [], []
    for i, (train_slice, test_slice) in enumerate(folds):
        X_train, y_train = X.iloc[train_slice], y.iloc[train_slice]
        X_test = X.iloc[test_slice]
        if y_train.nunique() < 2 or len(X_test) == 0:
            continue

        model = LogisticRegression(max_iter=1000)
        model.fit(X_train, y_train)
        baseline = DummyClassifier(strategy="prior")
        baseline.fit(X_train, y_train)

        proba_full[test_slice, :] = _align_proba(model, X_test, classes)
        baseline_proba_full[test_slice, :] = _align_proba(baseline, X_test, classes)
        fold_coefs.append(_align_coef(model, classes, list(X.columns)))
        fold_meta.append(
            {
                "fold": i, "test_index": index_valid[test_slice], "train_index": index_valid[train_slice],
                "n_train": len(X_train), "n_test": len(X_test),
            }
        )

    proba_df = pd.DataFrame(proba_full, index=index_valid, columns=classes).reindex(features.index)
    baseline_df = pd.DataFrame(baseline_proba_full, index=index_valid, columns=classes).reindex(features.index)
    return WalkForwardPredictions(
        classes=classes, proba=proba_df, baseline_proba=baseline_df, fold_coefs=fold_coefs, fold_meta=fold_meta
    )


def walk_forward_oos_proba(
    features: pd.DataFrame,
    labels: pd.Series,
    n_folds: int = DEFAULT_N_FOLDS,
    min_train_fraction: float = DEFAULT_MIN_TRAIN_FRACTION,
) -> pd.DataFrame:
    """Thin convenience wrapper around `walk_forward_predict` for callers
    (e.g. `mnq_system.strategies.model_driven`) that only need the model's
    own out-of-sample class probabilities, not the baseline/coefficients/
    fold metadata `evaluate_horizon` also uses.
    """
    return walk_forward_predict(features, labels, n_folds, min_train_fraction).proba


def causal_confidence_percentile(wf: WalkForwardPredictions) -> pd.Series:
    """Per-fold, percentile-rank that fold's own top-1 OOS confidence
    against the concatenation of top-1 confidences from OOS test folds
    0..i-1 only (never in-sample/training confidence, which is
    overconfident, and never a later fold's confidence, which doesn't
    exist yet) -- so a horizon's confidence becomes a comparable "how
    confident is this model right now relative to its own past OOS calls"
    scale, regardless of that horizon's own label base-rate (raw top-1
    probability is NOT comparable across horizons whose label
    distributions have different base-rate skew -- see
    mnq_system.strategies.model_driven).

    NaN for fold 0's rows (no prior OOS history yet to rank against) and
    for every row before fold 0 begins, same convention as
    `walk_forward_predict`'s NaN-before-first-fold.
    """
    percentile = pd.Series(np.nan, index=wf.proba.index)
    top1_conf = wf.proba.max(axis=1)

    reference_chunks: list = []
    for meta in wf.fold_meta:
        test_index = meta["test_index"]
        if reference_chunks:
            reference = np.sort(np.concatenate(reference_chunks))
            fold_conf = top1_conf.loc[test_index].to_numpy()
            percentile.loc[test_index] = np.searchsorted(reference, fold_conf, side="left") / len(reference)
        reference_chunks.append(top1_conf.loc[test_index].to_numpy())

    return percentile


def causal_expected_value(wf: WalkForwardPredictions, labels: pd.Series, continuous_return: pd.Series) -> pd.DataFrame:
    """Turns the model's full predicted class-probability distribution into
    a continuous expected-value estimate, rather than reducing it to a
    top-1 class first (see mnq_system.strategies.model_driven.ev_single_horizon
    for why this matters for decision-making, not just classification
    accuracy): `EV(t) = sum_c P(c|state_t) * E[continuous_return | class=c]`.

    `E[continuous_return | class=c]` is estimated causally, per fold, using
    ONLY that fold's own `train_index` rows (never test rows, never a later
    fold) -- the same expanding-fold discipline `walk_forward_predict`
    already enforces for the classifier itself. A class entirely absent
    from a fold's training data defaults to 0.0 ("no historical evidence,
    treat as return-neutral"), not an error.

    Also returns the matching predictive variance,
    `Var(t) = sum_c P(c|state_t) * (E[return|class=c] - EV(t))^2` -- a
    causal uncertainty estimate alongside EV, reported as a diagnostic in
    this phase (not yet consumed by any decision rule).

    NaN for every row before fold 0 begins, same convention as
    `walk_forward_predict`'s NaN-before-first-fold.
    """
    ev = pd.Series(np.nan, index=wf.proba.index)
    variance = pd.Series(np.nan, index=wf.proba.index)

    for meta in wf.fold_meta:
        train_index, test_index = meta["train_index"], meta["test_index"]

        train_labels = labels.loc[train_index].astype(int)
        train_returns = continuous_return.loc[train_index]
        class_return_by_class = train_returns.groupby(train_labels).mean()
        class_return_vec = np.array([class_return_by_class.get(c, 0.0) for c in wf.proba.columns])

        test_proba = wf.proba.loc[test_index].to_numpy()
        ev_test = test_proba @ class_return_vec
        diff_sq = (class_return_vec[np.newaxis, :] - ev_test[:, np.newaxis]) ** 2
        var_test = (test_proba * diff_sq).sum(axis=1)

        ev.loc[test_index] = ev_test
        variance.loc[test_index] = var_test

    return pd.DataFrame({"ev": ev, "variance": variance})


def per_row_neg_log_likelihood(y_true: np.ndarray, proba: np.ndarray, classes: np.ndarray) -> np.ndarray:
    """`-log(p[row, true_class[row]])` per row, clipped away from 0 to
    avoid -inf on a confidently-wrong prediction. Log-loss is the mean of
    this array -- kept as a per-row array (not pre-averaged) specifically
    so it can be bootstrap-resampled by `mnq_system.backtest.stats.bootstrap_confidence`.
    """
    class_to_col = {c: i for i, c in enumerate(classes)}
    cols = np.array([class_to_col[y] for y in y_true])
    p = np.clip(proba[np.arange(len(y_true)), cols], 1e-15, 1.0)
    return -np.log(p)


def per_row_brier_score(y_true: np.ndarray, proba: np.ndarray, classes: np.ndarray) -> np.ndarray:
    """Multiclass Brier score per row: sum over every class of
    (predicted_probability - actual_indicator)^2. The mean of this array is
    the Brier score -- unlike log-loss, it stays finite and bounded ([0,2])
    even for a confidently wrong prediction, so it's a useful cross-check
    on log-loss rather than a duplicate of it.
    """
    class_to_col = {c: i for i, c in enumerate(classes)}
    one_hot = np.zeros_like(proba)
    rows = np.arange(len(y_true))
    cols = np.array([class_to_col[y] for y in y_true])
    one_hot[rows, cols] = 1.0
    return ((proba - one_hot) ** 2).sum(axis=1)


def _calibration_table(
    y_true: np.ndarray, proba: np.ndarray, classes: np.ndarray, n_buckets: int = DEFAULT_CALIBRATION_BUCKETS
) -> pd.DataFrame:
    """Per class: bucket the model's predicted P(class) into `n_buckets`
    quantile buckets, report mean predicted probability vs actual
    frequency of that class within the bucket -- a standard reliability
    diagram, one row per (class, bucket).
    """
    rows = []
    for ci, c in enumerate(classes):
        p = proba[:, ci]
        actual = (y_true == c).astype(int)
        try:
            bucket = pd.qcut(p, n_buckets, duplicates="drop")
        except ValueError:
            continue
        grouped = pd.DataFrame({"p": p, "actual": actual, "bucket": bucket}).groupby("bucket", observed=True)
        table = grouped.agg(mean_predicted=("p", "mean"), actual_frequency=("actual", "mean"), n=("actual", "size"))
        table = table.reset_index(drop=True)
        table["class"] = c
        rows.append(table)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def expected_calibration_error(calibration_table: pd.DataFrame) -> pd.DataFrame:
    """Per class: the n-weighted mean absolute gap between predicted
    probability and actual frequency across `_calibration_table`'s
    buckets -- a single "how well-calibrated is this class" number (0 =
    perfect), summarizing the full reliability diagram.
    """
    if calibration_table.empty:
        return pd.DataFrame(columns=["class", "ece"])

    def _ece(group: pd.DataFrame) -> float:
        gap = (group["mean_predicted"] - group["actual_frequency"]).abs()
        return float((gap * group["n"]).sum() / group["n"].sum())

    rows = [{"class": c, "ece": _ece(g)} for c, g in calibration_table.groupby("class")]
    return pd.DataFrame(rows)


def _coefficient_stability(fold_coefs: list) -> pd.DataFrame:
    """Across folds, for every (feature, class): mean coefficient, std,
    and sign-consistency (fraction of folds whose sign matches the
    majority sign across all folds) -- a feature whose sign flips fold to
    fold is not a stable relationship, however strong its aggregate/global
    coefficient looks.
    """
    if not fold_coefs:
        return pd.DataFrame()

    stacked = pd.concat(fold_coefs, keys=range(len(fold_coefs)), names=["fold"])
    rows = []
    for class_name in stacked.index.get_level_values(1).unique():
        for feature in stacked.columns:
            values = stacked.xs(class_name, level=1)[feature].dropna()
            if values.empty:
                continue
            signs = np.sign(values)
            majority_sign = signs.mode().iloc[0] if not signs.mode().empty else 0
            sign_consistency = float((signs == majority_sign).mean()) if majority_sign != 0 else float("nan")
            rows.append(
                {
                    "class": class_name, "feature": feature, "mean_coef": float(values.mean()),
                    "std_coef": float(values.std()), "sign_consistency": sign_consistency, "n_folds": len(values),
                }
            )
    return pd.DataFrame(rows)


def _bucketed_improvement(
    improvement: np.ndarray, bucket_labels: pd.Series, min_bucket_n: int = 30
) -> pd.DataFrame:
    """Bootstrap CI on `improvement`, computed separately within each value
    of `bucket_labels` -- the regime-conditional / per-year breakdown.
    Buckets smaller than `min_bucket_n` are skipped (too few rows for a
    meaningful bootstrap CI, same reasoning as the small-sample warning in
    mnq_system.backtest.stats).
    """
    rows = []
    df = pd.DataFrame({"improvement": improvement, "bucket": bucket_labels.to_numpy()})
    for bucket_value, group in df.groupby("bucket"):
        if len(group) < min_bucket_n:
            continue
        boot = bootstrap_confidence(group["improvement"].tolist())
        rows.append(
            {
                "bucket": bucket_value, "n": len(group), "mean_improvement": boot["mean"],
                "ci_low": boot["ci_low"], "ci_high": boot["ci_high"], "prob_improvement_le_zero": boot["prob_mean_le_zero"],
            }
        )
    return pd.DataFrame(rows)


def default_class_direction(classes: np.ndarray) -> dict:
    """Assumes `classes` are ordered bin indices from most-bearish to
    most-bullish (mnq_system.modeling.labels.build_return_bin_labels's
    convention) -- the middle bin (only when there's an odd number of
    classes) is direction-neutral ("flat"), everything below is -1
    (bearish), everything above +1 (bullish).
    """
    n = len(classes)
    mid = (n - 1) / 2
    direction = {}
    for i, c in enumerate(classes):
        if n % 2 == 1 and i == mid:
            direction[c] = 0
        elif i < mid:
            direction[c] = -1
        else:
            direction[c] = 1
    return direction


def _confidence_decile_raw(
    y_true: np.ndarray,
    proba: np.ndarray,
    classes: np.ndarray,
    continuous_return: Optional[np.ndarray] = None,
    class_direction: Optional[dict] = None,
    n_deciles: int = DEFAULT_CONFIDENCE_DECILES,
) -> pd.DataFrame:
    """One row per OOS prediction: the model's own top-1 predicted
    probability ("confidence"), whether that top-1 class was actually
    correct, and (if `continuous_return` is supplied) the realized
    ATR-normalized forward return signed by the direction the top-1 class
    implies -- "what would have happened if you traded the model's single
    most likely outcome." Ranked into `n_deciles` quantile buckets by
    confidence.
    """
    top1_idx = proba.argmax(axis=1)
    top1_class = classes[top1_idx]
    confidence = proba[np.arange(len(proba)), top1_idx]
    correct = (top1_class == y_true).astype(int)

    df = pd.DataFrame({"confidence": confidence, "correct": correct})
    if continuous_return is not None:
        direction_map = class_direction or default_class_direction(classes)
        direction = np.array([direction_map[c] for c in top1_class])
        df["direction"] = direction
        df["signed_return"] = direction * continuous_return

    try:
        df["decile"] = pd.qcut(df["confidence"], n_deciles, labels=False, duplicates="drop")
    except ValueError:
        df["decile"] = np.nan
    return df


def _directional_confidence_decile_raw(raw: pd.DataFrame, n_deciles: int = DEFAULT_CONFIDENCE_DECILES) -> pd.DataFrame:
    """Restricts `_confidence_decile_raw`'s output to rows where the
    model's top-1 prediction was an actual directional call (direction !=
    0, i.e. not the flat/neutral class), and re-buckets deciles within just
    that subset. "Confident about flat" and "confident about a directional
    bet" are different claims -- at short horizons flat dominates almost
    every row regardless of confidence, which would otherwise make the
    signed-return metric measure near-zero noise diluted by a huge
    non-directional majority, not the thing it's meant to measure: does
    confidence rank usefully among the calls that would actually be traded.
    """
    if raw.empty or "direction" not in raw.columns:
        return pd.DataFrame()
    directional = raw.loc[raw["direction"] != 0].copy()
    if directional.empty:
        return directional
    try:
        directional["decile"] = pd.qcut(directional["confidence"], n_deciles, labels=False, duplicates="drop")
    except ValueError:
        directional["decile"] = np.nan
    return directional


def _confidence_decile_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Per confidence decile: mean confidence, top-1 accuracy, and (if
    available) mean signed realized return -- the aggregate view of
    `_confidence_decile_raw`.
    """
    if raw.empty or raw["decile"].isna().all():
        return pd.DataFrame()
    valid = raw.dropna(subset=["decile"])
    agg = {"confidence": "mean", "correct": "mean"}
    if "signed_return" in valid.columns:
        agg["signed_return"] = "mean"
    table = valid.groupby("decile").agg(agg).rename(columns={"confidence": "mean_confidence", "correct": "accuracy"})
    table["n"] = valid.groupby("decile").size()
    return table.reset_index().sort_values("decile").reset_index(drop=True)


_NAN_SPREAD = {"mean_diff": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "prob_diff_le_zero": float("nan")}


def _bootstrap_decile_spread(
    raw: pd.DataFrame, value_col: str, n_resamples: int = 2000, ci: float = 0.90, seed: int = 42
) -> dict:
    """Bootstrap CI on (top-decile mean - bottom-decile mean) of
    `value_col` -- is the extreme-confidence spread real, or noise?
    Independent resampling within each of the two extreme deciles (a
    two-sample bootstrap), same percentile-CI convention as
    mnq_system.backtest.stats.bootstrap_confidence.
    """
    if raw.empty or "decile" not in raw.columns or value_col not in raw.columns or raw["decile"].dropna().empty:
        return dict(_NAN_SPREAD)
    low_decile, high_decile = raw["decile"].min(), raw["decile"].max()
    if low_decile == high_decile:
        return dict(_NAN_SPREAD)
    low_vals = raw.loc[raw["decile"] == low_decile, value_col].dropna().to_numpy()
    high_vals = raw.loc[raw["decile"] == high_decile, value_col].dropna().to_numpy()
    if len(low_vals) == 0 or len(high_vals) == 0:
        return dict(_NAN_SPREAD)

    rng = np.random.default_rng(seed)
    low_means = low_vals[rng.integers(0, len(low_vals), size=(n_resamples, len(low_vals)))].mean(axis=1)
    high_means = high_vals[rng.integers(0, len(high_vals), size=(n_resamples, len(high_vals)))].mean(axis=1)
    diffs = high_means - low_means
    alpha = (1 - ci) / 2
    return {
        "mean_diff": float(diffs.mean()), "ci_low": float(np.quantile(diffs, alpha)),
        "ci_high": float(np.quantile(diffs, 1 - alpha)), "prob_diff_le_zero": float((diffs <= 0).mean()),
    }


_NAN_MONOTONICITY = {"spearman_r": float("nan"), "p_value": float("nan")}


def _monotonicity_correlation(raw: pd.DataFrame, value_col: str) -> dict:
    """Spearman rank correlation between confidence and `value_col` over
    every individual OOS row (not just the 10 decile means) -- "is there a
    monotonic relationship between predicted probability and realized
    outcome," directly, with a p-value.
    """
    if raw.empty or value_col not in raw.columns:
        return dict(_NAN_MONOTONICITY)
    valid = raw[["confidence", value_col]].dropna()
    if len(valid) < 10:
        return dict(_NAN_MONOTONICITY)
    r, p = spearmanr(valid["confidence"], valid[value_col])
    return {"spearman_r": float(r), "p_value": float(p)}


@dataclass
class HorizonEvalResult:
    horizon: int
    n_test: int
    model_log_loss: float
    baseline_log_loss: float
    improvement: float  # baseline_log_loss - model_log_loss, mean over bootstrap resamples
    ci_low: float
    ci_high: float
    prob_improvement_le_zero: float
    model_brier: float
    baseline_brier: float
    brier_improvement: float
    brier_ci_low: float
    brier_ci_high: float
    brier_prob_improvement_le_zero: float
    folds: pd.DataFrame
    calibration: pd.DataFrame
    calibration_error: pd.DataFrame = field(default_factory=pd.DataFrame)
    coefficient_stability: pd.DataFrame = field(default_factory=pd.DataFrame)
    regime_breakdown: pd.DataFrame = field(default_factory=pd.DataFrame)
    year_breakdown: pd.DataFrame = field(default_factory=pd.DataFrame)
    confidence_deciles: pd.DataFrame = field(default_factory=pd.DataFrame)
    accuracy_decile_spread: dict = field(default_factory=lambda: dict(_NAN_SPREAD))
    return_decile_spread: dict = field(default_factory=lambda: dict(_NAN_SPREAD))
    accuracy_monotonicity: dict = field(default_factory=lambda: dict(_NAN_MONOTONICITY))
    return_monotonicity: dict = field(default_factory=lambda: dict(_NAN_MONOTONICITY))
    directional_call_fraction: float = float("nan")  # fraction of OOS rows where top-1 predicted a real direction
    directional_confidence_deciles: pd.DataFrame = field(default_factory=pd.DataFrame)
    directional_return_decile_spread: dict = field(default_factory=lambda: dict(_NAN_SPREAD))
    directional_return_monotonicity: dict = field(default_factory=lambda: dict(_NAN_MONOTONICITY))

    def summary_text(self) -> str:
        lines = [
            f"=== Horizon {self.horizon} bars ===",
            f"OOS rows:                  {self.n_test}",
            f"Model log-loss:            {self.model_log_loss:.4f}",
            f"Baseline (prior) log-loss: {self.baseline_log_loss:.4f}",
            f"Log-loss improvement (baseline - model): {self.improvement:+.4f}",
            f"90% bootstrap CI on log-loss improvement: [{self.ci_low:+.4f}, {self.ci_high:+.4f}]"
            f"  (P(improvement<=0) ~ {self.prob_improvement_le_zero:.1%})",
            f"Model Brier / baseline Brier: {self.model_brier:.4f} / {self.baseline_brier:.4f}"
            f"  (improvement {self.brier_improvement:+.4f}, 90% CI [{self.brier_ci_low:+.4f}, {self.brier_ci_high:+.4f}],"
            f" P(improvement<=0) ~ {self.brier_prob_improvement_le_zero:.1%})",
        ]
        if not self.folds.empty:
            frac_positive = (self.folds["improvement"] > 0).mean()
            lines.append(
                f"Per-fold: {len(self.folds)} folds, {frac_positive:.0%} with positive improvement "
                f"(mean={self.folds['improvement'].mean():+.4f}, std={self.folds['improvement'].std():.4f})"
            )
        if not self.calibration_error.empty:
            lines.append("Calibration error (ECE) per class, lower is better-calibrated:")
            for _, row in self.calibration_error.iterrows():
                lines.append(f"  class {int(row['class'])}: {row['ece']:.4f}")
        if not self.coefficient_stability.empty:
            unstable = self.coefficient_stability[self.coefficient_stability["sign_consistency"] < 0.75]
            n_features = self.coefficient_stability["feature"].nunique()
            lines.append(
                f"Coefficient stability: {n_features} features x {self.coefficient_stability['class'].nunique()} classes; "
                f"{len(unstable)} (feature, class) pairs with sign_consistency < 75% across folds"
            )
        if not self.regime_breakdown.empty:
            lines.append("Log-loss improvement by ATR-regime tercile:")
            for _, row in self.regime_breakdown.iterrows():
                lines.append(
                    f"  {row['bucket']}: n={int(row['n'])} improvement={row['mean_improvement']:+.4f} "
                    f"[{row['ci_low']:+.4f}, {row['ci_high']:+.4f}] P(<=0)~{row['prob_improvement_le_zero']:.1%}"
                )
        if not self.year_breakdown.empty:
            lines.append("Log-loss improvement by calendar year:")
            for _, row in self.year_breakdown.iterrows():
                lines.append(
                    f"  {row['bucket']}: n={int(row['n'])} improvement={row['mean_improvement']:+.4f} "
                    f"[{row['ci_low']:+.4f}, {row['ci_high']:+.4f}] P(<=0)~{row['prob_improvement_le_zero']:.1%}"
                )
        if not self.confidence_deciles.empty:
            lines.append("Confidence deciles (0=least confident, ranked by the model's own top-1 probability):")
            cols = ["decile", "n", "mean_confidence", "accuracy"]
            has_return = "signed_return" in self.confidence_deciles.columns
            if has_return:
                cols.append("signed_return")
            lines.append(self.confidence_deciles[cols].to_string(index=False))
            lines.append(
                f"Top-vs-bottom decile accuracy spread: {self.accuracy_decile_spread['mean_diff']:+.4f} "
                f"[{self.accuracy_decile_spread['ci_low']:+.4f}, {self.accuracy_decile_spread['ci_high']:+.4f}]"
                f"  (P(spread<=0) ~ {self.accuracy_decile_spread['prob_diff_le_zero']:.1%})"
            )
            lines.append(
                f"Confidence-vs-accuracy monotonicity: Spearman r={self.accuracy_monotonicity['spearman_r']:+.3f}"
                f" (p={self.accuracy_monotonicity['p_value']:.2e})"
            )
            if has_return:
                lines.append(
                    f"Top-vs-bottom decile signed-return spread (all rows, flat predictions count as 0): "
                    f"{self.return_decile_spread['mean_diff']:+.4f} "
                    f"[{self.return_decile_spread['ci_low']:+.4f}, {self.return_decile_spread['ci_high']:+.4f}]"
                    f"  (P(spread<=0) ~ {self.return_decile_spread['prob_diff_le_zero']:.1%})"
                )
                lines.append(
                    f"Confidence-vs-return monotonicity (all rows): Spearman r={self.return_monotonicity['spearman_r']:+.3f}"
                    f" (p={self.return_monotonicity['p_value']:.2e})"
                )
        if not self.directional_confidence_deciles.empty:
            lines.append(
                f"Directional-call-only confidence deciles ({self.directional_call_fraction:.1%} of rows were an "
                f"actual up/down call, not 'flat' -- re-bucketed by confidence within just that subset):"
            )
            lines.append(self.directional_confidence_deciles[["decile", "n", "mean_confidence", "signed_return"]].to_string(index=False))
            lines.append(
                f"Directional top-vs-bottom decile return spread: {self.directional_return_decile_spread['mean_diff']:+.4f} "
                f"[{self.directional_return_decile_spread['ci_low']:+.4f}, {self.directional_return_decile_spread['ci_high']:+.4f}]"
                f"  (P(spread<=0) ~ {self.directional_return_decile_spread['prob_diff_le_zero']:.1%})"
            )
            lines.append(
                f"Directional confidence-vs-return monotonicity: Spearman r={self.directional_return_monotonicity['spearman_r']:+.3f}"
                f" (p={self.directional_return_monotonicity['p_value']:.2e})"
            )
        return "\n".join(lines)


def _empty_result(horizon: int) -> HorizonEvalResult:
    nan = float("nan")
    return HorizonEvalResult(
        horizon=horizon, n_test=0, model_log_loss=nan, baseline_log_loss=nan, improvement=nan, ci_low=nan,
        ci_high=nan, prob_improvement_le_zero=nan, model_brier=nan, baseline_brier=nan, brier_improvement=nan,
        brier_ci_low=nan, brier_ci_high=nan, brier_prob_improvement_le_zero=nan, folds=pd.DataFrame(),
        calibration=pd.DataFrame(),
    )


def evaluate_horizon(
    features: pd.DataFrame,
    labels: pd.Series,
    horizon: int,
    n_folds: int = DEFAULT_N_FOLDS,
    min_train_fraction: float = DEFAULT_MIN_TRAIN_FRACTION,
    continuous_returns: Optional[pd.Series] = None,
) -> HorizonEvalResult:
    wf = walk_forward_predict(features, labels, n_folds, min_train_fraction)
    classes = wf.classes
    labels_int = labels.astype("Int64")  # nullable int -- keeps NaN rows distinguishable, matches wf.proba's index

    valid_mask = wf.proba.notna().all(axis=1)
    if not valid_mask.any():
        return _empty_result(horizon)

    y_all = labels_int.loc[valid_mask].astype(int).to_numpy()
    model_proba_all = wf.proba.loc[valid_mask].to_numpy()
    baseline_proba_all = wf.baseline_proba.loc[valid_mask].to_numpy()
    timestamps_all = wf.proba.loc[valid_mask].index

    fold_rows = []
    for meta in wf.fold_meta:
        test_index = meta["test_index"]
        y_test = labels_int.loc[test_index].astype(int).to_numpy()
        model_p = wf.proba.loc[test_index].to_numpy()
        baseline_p = wf.baseline_proba.loc[test_index].to_numpy()
        model_nll = per_row_neg_log_likelihood(y_test, model_p, classes)
        baseline_nll = per_row_neg_log_likelihood(y_test, baseline_p, classes)
        fold_rows.append(
            {
                "fold": meta["fold"], "n_train": meta["n_train"], "n_test": meta["n_test"],
                "model_log_loss": float(model_nll.mean()), "baseline_log_loss": float(baseline_nll.mean()),
                "improvement": float((baseline_nll - model_nll).mean()),
            }
        )

    model_nll_all = per_row_neg_log_likelihood(y_all, model_proba_all, classes)
    baseline_nll_all = per_row_neg_log_likelihood(y_all, baseline_proba_all, classes)
    ll_improvement_all = baseline_nll_all - model_nll_all

    model_brier_all = per_row_brier_score(y_all, model_proba_all, classes)
    baseline_brier_all = per_row_brier_score(y_all, baseline_proba_all, classes)
    brier_improvement_all = baseline_brier_all - model_brier_all

    ll_boot = bootstrap_confidence(ll_improvement_all.tolist())
    brier_boot = bootstrap_confidence(brier_improvement_all.tolist())
    calibration = _calibration_table(y_all, model_proba_all, classes)
    calibration_error = expected_calibration_error(calibration)
    coefficient_stability = _coefficient_stability(wf.fold_coefs)

    regime_breakdown = pd.DataFrame()
    if REGIME_COLUMN in features.columns:
        regime_all = features.loc[timestamps_all, REGIME_COLUMN].to_numpy()
        try:
            regime_bucket = pd.cut(
                regime_all, bins=[-0.001, 1 / 3, 2 / 3, 1.001], labels=["low_vol", "mid_vol", "high_vol"]
            )
            regime_breakdown = _bucketed_improvement(ll_improvement_all, pd.Series(regime_bucket))
        except ValueError:
            pass

    year_series = pd.Series(pd.DatetimeIndex(timestamps_all).year)
    year_breakdown = _bucketed_improvement(ll_improvement_all, year_series)

    continuous_return_all = continuous_returns.loc[timestamps_all].to_numpy() if continuous_returns is not None else None
    confidence_raw = _confidence_decile_raw(y_all, model_proba_all, classes, continuous_return=continuous_return_all)
    confidence_deciles = _confidence_decile_table(confidence_raw)
    accuracy_decile_spread = _bootstrap_decile_spread(confidence_raw, "correct")
    accuracy_monotonicity = _monotonicity_correlation(confidence_raw, "correct")
    return_decile_spread = _bootstrap_decile_spread(confidence_raw, "signed_return")
    return_monotonicity = _monotonicity_correlation(confidence_raw, "signed_return")

    directional_raw = _directional_confidence_decile_raw(confidence_raw)
    directional_call_fraction = (
        float(len(directional_raw)) / len(confidence_raw) if len(confidence_raw) > 0 else float("nan")
    )
    directional_confidence_deciles = _confidence_decile_table(directional_raw)
    directional_return_decile_spread = _bootstrap_decile_spread(directional_raw, "signed_return")
    directional_return_monotonicity = _monotonicity_correlation(directional_raw, "signed_return")

    return HorizonEvalResult(
        horizon=horizon,
        n_test=len(y_all),
        model_log_loss=float(model_nll_all.mean()),
        baseline_log_loss=float(baseline_nll_all.mean()),
        improvement=ll_boot["mean"],
        ci_low=ll_boot["ci_low"],
        ci_high=ll_boot["ci_high"],
        prob_improvement_le_zero=ll_boot["prob_mean_le_zero"],
        model_brier=float(model_brier_all.mean()),
        baseline_brier=float(baseline_brier_all.mean()),
        brier_improvement=brier_boot["mean"],
        brier_ci_low=brier_boot["ci_low"],
        brier_ci_high=brier_boot["ci_high"],
        brier_prob_improvement_le_zero=brier_boot["prob_mean_le_zero"],
        folds=pd.DataFrame(fold_rows),
        calibration=calibration,
        calibration_error=calibration_error,
        coefficient_stability=coefficient_stability,
        regime_breakdown=regime_breakdown,
        year_breakdown=year_breakdown,
        confidence_deciles=confidence_deciles,
        accuracy_decile_spread=accuracy_decile_spread,
        return_decile_spread=return_decile_spread,
        accuracy_monotonicity=accuracy_monotonicity,
        return_monotonicity=return_monotonicity,
        directional_call_fraction=directional_call_fraction,
        directional_confidence_deciles=directional_confidence_deciles,
        directional_return_decile_spread=directional_return_decile_spread,
        directional_return_monotonicity=directional_return_monotonicity,
    )


def evaluate_all_horizons(
    features: pd.DataFrame,
    labels_by_horizon: dict,
    n_folds: int = DEFAULT_N_FOLDS,
    min_train_fraction: float = DEFAULT_MIN_TRAIN_FRACTION,
    continuous_returns_by_horizon: Optional[dict] = None,
) -> dict:
    continuous_returns_by_horizon = continuous_returns_by_horizon or {}
    return {
        h: evaluate_horizon(
            features, labels, h, n_folds, min_train_fraction,
            continuous_returns=continuous_returns_by_horizon.get(h),
        )
        for h, labels in labels_by_horizon.items()
    }
