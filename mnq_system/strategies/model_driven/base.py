"""
Shared base for decision-level EV strategies: the first place a
walk-forward-validated predictive model (mnq_system/modeling/) becomes an
actual `Strategy` pluggable into `BacktestEngine`, closing the loop from
prediction back into decisions and trades.

Reuses `HypothesisStrategy`'s already-standardized ATR-stop + fixed-R-target
entry sizing (mnq_system/strategies/hypotheses/base.py). Exit itself is
pluggable (`ModelDrivenConfig.exit_mode`) -- the standardized fixed-R exit
remains the default/primary comparison basis throughout this project, but a
horizon-matched time exit is also available so "does exit shape matter"
can be tested with *exactly* the same signal calendar, isolating that one
variable.

Two confounds were found and fixed in the first decision-level EV pass
(see project_model_driven_ev_investigation.md memory):

1. Raw top-1 class probability is NOT comparable across horizons whose
   label distributions have different base-rate skew (longer horizons'
   ATR-normalized-return labels are more concentrated in the extreme bins,
   inflating raw confidence regardless of real skill) -- fixed by
   `mnq_system.modeling.evaluate.causal_confidence_percentile`, which
   ranks each horizon's confidence against its own prior OOS history
   instead of using the raw magnitude.
2. Every bar was treated as an independent trial, even though predictions
   are serially correlated -- fixed by precomputing a full, non-overlapping
   **signal calendar** once (mnq_system.strategies.model_driven.
   signal_selectors) instead of re-deciding bar-by-bar inside `detect_event`.
   `combine_horizon_signals` still does the per-bar combination arithmetic
   (and still owns its own confidence-threshold gating), but its raw
   per-bar output is now debounced/selected before any bar is offered to
   the engine as an actual entry signal.

The user was explicit that *how* to combine the 10/20/40-bar horizons when
they disagree, and *which* single bar within a correlated streak to act on,
are both untested hypotheses, not something to assume -- so this base class
does only the shared, policy-agnostic work and delegates to two
independently-swappable pieces: `combine_horizon_signals` (implemented once
per combination policy, in its own module) and `signal_selector` (one of
`SIGNAL_SELECTORS`, chosen via config/CLI, shared by every policy).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from mnq_system.candlesticks import Bar
from mnq_system.config import AccountConfig
from mnq_system.indicators import atr
from mnq_system.modeling.evaluate import causal_confidence_percentile, default_class_direction, walk_forward_predict
from mnq_system.modeling.features import DEFAULT_FEATURE_CONFIG, FeatureConfig, build_feature_matrix
from mnq_system.modeling.labels import build_return_bin_labels
from mnq_system.strategies.common import simple_stop_target_exit
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig, HypothesisStrategy, exit_cfg_from_args
from mnq_system.strategies.model_driven.signal_selectors import (
    DEFAULT_DEBOUNCE_BARS,
    SIGNAL_SELECTORS,
)
from mnq_system.strategy_api import ExitDecision, MarketSnapshot, Position

DEFAULT_HORIZONS = (10, 20, 40)
# Percentile scale (0-1), comparable across horizons by construction --
# 0.7 means "in the top 30% most confident calls this specific horizon has
# made," a starting point pending a fresh decile-style sensitivity check,
# CLI-overridable.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7
EXIT_MODES = ("fixed_r", "horizon_time")


@dataclass(frozen=True)
class ModelDrivenConfig:
    entry_timeframe: str = "5m"
    exit: HypothesisExitConfig = field(default_factory=HypothesisExitConfig)
    horizons: tuple = DEFAULT_HORIZONS
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    feature_cfg: FeatureConfig = field(default_factory=lambda: DEFAULT_FEATURE_CONFIG)
    n_folds: int = 8
    min_train_fraction: float = 0.2
    signal_selector: str = "rising_edge"  # one of SIGNAL_SELECTORS -- itself an untested hypothesis, CLI-overridable
    debounce_bars: int = DEFAULT_DEBOUNCE_BARS
    # "fixed_r": inherited ATR-stop + 1.5R target (the project's standard).
    # "horizon_time": same ATR stop, but holds for exactly the firing
    # signal's owning_horizon bars instead of a fixed R target -- isolates
    # the profit-taking/duration variable, same signal calendar either way.
    exit_mode: str = "fixed_r"


def horizon_time_exit(position: Position, bar: Bar, current_bar_pos: int) -> ExitDecision:
    """Same protective ATR stop as `simple_stop_target_exit` (risk sizing
    unchanged -- only the profit-taking mechanism differs), but no fixed R
    target: holds for exactly `position.context["owning_horizon"]` bars
    (the horizon the firing signal actually came from) before exiting at
    market. `entry_bar_pos`/`owning_horizon` are populated by
    `ModelDrivenStrategy.build_context` at signal time.
    """
    is_long = position.direction == "long"
    stop_hit = bar.low <= position.stop_price if is_long else bar.high >= position.stop_price
    if stop_hit:
        return ExitDecision(action="stop", fill_price=position.stop_price, fraction=1.0)

    entry_bar_pos = position.context["entry_bar_pos"]
    owning_horizon = position.context["owning_horizon"]
    if current_bar_pos - entry_bar_pos >= owning_horizon:
        return ExitDecision(action="time_exit", fill_price=bar.close, fraction=1.0)

    return ExitDecision(action="none")


class SignalCalendarMixin:
    """Shared calendar-lookup/exit-dispatch plumbing for any `HypothesisStrategy`
    subclass that precomputes a non-overlapping `(direction, owning_horizon)`
    signal calendar once in `on_precompute` (via one of `SIGNAL_SELECTORS`)
    and consumes it via a pure per-bar lookup rather than recomputing a
    decision bar-by-bar. Used by both `ModelDrivenStrategy` (threshold-based
    combination policies) and `mnq_system.strategies.model_driven.
    ev_single_horizon.EVSingleHorizonStrategy` (expected-value-based) --
    the calendar-consumption mechanics are identical regardless of how the
    calendar's raw per-bar signal was computed.

    Requires the concrete class to set `self._signal_calendar` (a DataFrame
    with "direction" [-1/0/+1] and "owning_horizon" columns, indexed like
    `self.bars_entry`) during `on_precompute`, and `self.cfg.exit_mode` to
    be one of `EXIT_MODES`.
    """

    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        j = snapshot.timeframes["entry"].pos
        direction_val = self._signal_calendar["direction"].iloc[j]
        if direction_val == 0:
            return None
        self._last_owning_horizon = int(self._signal_calendar["owning_horizon"].iloc[j])
        return "long" if direction_val == 1 else "short"

    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        bar = snapshot.timeframes["entry"].bar(0)
        if self.cfg.exit_mode == "horizon_time":
            return horizon_time_exit(position, bar, snapshot.timeframes["entry"].pos)
        return simple_stop_target_exit(position, bar)


class ModelDrivenStrategy(SignalCalendarMixin, HypothesisStrategy):
    """`timeframes`/entry sizing are inherited unchanged from
    `HypothesisStrategy`. Subclasses (one per candidate combination policy)
    implement only `combine_horizon_signals`.
    """

    def __init__(self, cfg: ModelDrivenConfig, account: AccountConfig):
        self.cfg = cfg
        super().__init__(
            cfg.exit, account, entry_timeframe=cfg.entry_timeframe,
            warmup_bars=cfg.feature_cfg.volatility_lookback_bars,
        )
        self._direction_by_horizon: dict = {}
        self._percentile_by_horizon: dict = {}
        self._signal_calendar: Optional[pd.DataFrame] = None  # columns: direction (-1/0/+1), owning_horizon
        self._last_owning_horizon: Optional[int] = None

    def on_precompute(self, full_history: dict) -> None:
        features = build_feature_matrix(full_history, self.account, self.cfg.feature_cfg)
        atr_series = atr(self.bars_entry, period=self.cfg.feature_cfg.atr_period)
        labels_by_horizon = build_return_bin_labels(self.bars_entry, atr_series, horizons=self.cfg.horizons)

        for h in self.cfg.horizons:
            wf = walk_forward_predict(
                features, labels_by_horizon[h], n_folds=self.cfg.n_folds, min_train_fraction=self.cfg.min_train_fraction
            )
            classes = np.array(sorted(wf.proba.columns))
            class_direction = default_class_direction(classes)
            # idxmax(axis=1) raises on an all-NaN row (the reserved
            # pre-fold-0 prefix, by construction never partially NaN) --
            # restrict to rows with at least one real value first.
            valid_rows = wf.proba.notna().any(axis=1)
            top1_class = pd.Series(np.nan, index=wf.proba.index)
            top1_class.loc[valid_rows] = wf.proba.loc[valid_rows].idxmax(axis=1)
            self._direction_by_horizon[h] = top1_class.map(class_direction)
            self._percentile_by_horizon[h] = causal_confidence_percentile(wf)

        self._signal_calendar = self._build_signal_calendar()

    def _build_raw_signal_series(self) -> pd.DataFrame:
        """Per bar: `combine_horizon_signals`'s raw decision (before any
        debouncing/selection), plus a `strength`/`owning_horizon` proxy
        (the single highest-percentile-confidence horizon that bar,
        regardless of which policy fired) -- NaN wherever any horizon
        lacks a prediction yet. `combine_horizon_signals` is only ever
        consulted once all `cfg.horizons` have a valid direction+percentile
        for that bar.
        """
        index = self.bars_entry.index
        raw_direction = pd.Series(np.nan, index=index)
        raw_strength = pd.Series(np.nan, index=index)
        raw_owning_horizon = pd.Series(np.nan, index=index)

        for pos in range(len(index)):
            horizon_directions, horizon_confidences = {}, {}
            complete = True
            for h in self.cfg.horizons:
                d = self._direction_by_horizon[h].iloc[pos]
                c = self._percentile_by_horizon[h].iloc[pos]
                if pd.isna(d) or pd.isna(c):
                    complete = False
                    break
                horizon_directions[h] = int(d)
                horizon_confidences[h] = float(c)
            if not complete:
                continue

            decision = self.combine_horizon_signals(horizon_directions, horizon_confidences)
            best_h = max(horizon_confidences, key=horizon_confidences.get)
            raw_direction.iloc[pos] = {"long": 1, "short": -1}.get(decision, 0)
            raw_strength.iloc[pos] = horizon_confidences[best_h]
            raw_owning_horizon.iloc[pos] = best_h

        return pd.DataFrame({"direction": raw_direction, "strength": raw_strength, "owning_horizon": raw_owning_horizon})

    def _build_signal_calendar(self) -> pd.DataFrame:
        raw = self._build_raw_signal_series()
        selector_fn = SIGNAL_SELECTORS[self.cfg.signal_selector]
        return selector_fn(raw, self.cfg.debounce_bars)

    def build_context(self, snapshot: MarketSnapshot, atr_val: float) -> dict:
        j = snapshot.timeframes["entry"].pos
        context = {
            "owning_horizon": self._last_owning_horizon,
            "entry_bar_pos": j,
            "signal_selector": self.cfg.signal_selector,
        }
        for h in self.cfg.horizons:
            context[f"direction_h{h}"] = self._direction_by_horizon[h].iloc[j]
            context[f"confidence_pctile_h{h}"] = self._percentile_by_horizon[h].iloc[j]
        return context

    def diagnostic_dimensions(self) -> list:
        return [f"direction_h{h}" for h in self.cfg.horizons] + ["owning_horizon"]

    # ---- subclass contract ----

    @abc.abstractmethod
    def combine_horizon_signals(self, horizon_directions: dict, horizon_confidences: dict) -> Optional[str]:
        """`horizon_directions`: dict[horizon -> -1/0/+1]. `horizon_confidences`:
        dict[horizon -> that horizon's confidence PERCENTILE (0-1, comparable
        across horizons) -- not the model's raw top-1 probability]. Returns
        "long", "short", or None (stand aside) -- the candidate combination
        policy under test. Should apply `self.cfg.confidence_threshold`
        itself (a single float on the percentile scale, shared by every
        horizon).
        """


def add_shared_model_cli_arguments(parser) -> None:
    """Registers the --model-* flags shared by every model_driven registry
    entry. Only ONE policy spec should reference this (see
    mnq_system/strategies/__init__.py) -- every registered strategy's
    add_cli_arguments runs against the same shared parser, and argparse
    errors on a flag being added twice. The other policy specs use
    mnq_system.strategies.common.noop_add_cli_arguments instead. Exit-shape
    flags (--hyp-*) are already registered by the hypotheses package and
    reused as-is (the standardized ATR-stop/R-target exit, still the
    default via --model-exit-mode fixed_r).
    """
    parser.add_argument("--model-n-folds", type=int, default=ModelDrivenConfig().n_folds)
    parser.add_argument("--model-min-train-fraction", type=float, default=ModelDrivenConfig().min_train_fraction)
    parser.add_argument("--model-conf-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    parser.add_argument("--model-signal-selector", choices=list(SIGNAL_SELECTORS), default=ModelDrivenConfig().signal_selector)
    parser.add_argument("--model-debounce-bars", type=int, default=ModelDrivenConfig().debounce_bars)
    parser.add_argument("--model-exit-mode", choices=list(EXIT_MODES), default=ModelDrivenConfig().exit_mode)


def base_cfg_from_args(args) -> ModelDrivenConfig:
    return ModelDrivenConfig(
        exit=exit_cfg_from_args(args),
        confidence_threshold=args.model_conf_threshold,
        n_folds=args.model_n_folds,
        min_train_fraction=args.model_min_train_fraction,
        signal_selector=args.model_signal_selector,
        debounce_bars=args.model_debounce_bars,
        exit_mode=args.model_exit_mode,
    )
