"""
Decision policy: expected-value based, single horizon, NO cross-horizon
aggregation. Tests a fundamentally different question from every other
policy in this package: instead of reducing a horizon's predicted class
distribution to (top-1 direction, scalar confidence) before any decision
logic runs (what `ModelDrivenStrategy`'s combination policies all do), this
strategy computes a continuous expected value directly from the FULL
predicted distribution (`mnq_system.modeling.evaluate.causal_expected_value`)
and compares it against the actual realistic round-trip cost of trading,
expressed in the same units, recomputed every bar from the current ATR.

Per the user's explicit direction: horizons are tested independently (h10,
h20, h40 via `--ev-horizon`), not combined -- how best to combine multiple
horizons' EV is deliberately deferred to its own future research phase, not
decided here by an untested aggregation formula. The predictive model
itself is completely frozen (same features, same walk-forward retrain
loop, same n_folds/min_train_fraction) -- only the entry decision rule
differs from `ModelDrivenStrategy`. Exit, signal-selector, and cost
assumptions are reused unchanged via `SignalCalendarMixin` (shared with
`ModelDrivenStrategy` -- see mnq_system.strategies.model_driven.base).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from mnq_system.candlesticks import Bar
from mnq_system.config import AccountConfig
from mnq_system.indicators import atr
from mnq_system.modeling.evaluate import causal_expected_value, walk_forward_predict
from mnq_system.modeling.features import DEFAULT_FEATURE_CONFIG, FeatureConfig, build_feature_matrix
from mnq_system.modeling.labels import build_return_bin_labels, forward_return_atr
from mnq_system.strategies.hypotheses.base import HypothesisExitConfig, HypothesisStrategy, exit_cfg_from_args
from mnq_system.strategies.model_driven.base import SignalCalendarMixin
from mnq_system.strategies.model_driven.signal_selectors import DEFAULT_DEBOUNCE_BARS, SIGNAL_SELECTORS
from mnq_system.strategy_api import ExitDecision, MarketSnapshot, Position

DEFAULT_HORIZON = 40
HORIZON_CHOICES = (10, 20, 40)
# Local to this module, not the shared base.EXIT_MODES/--model-exit-mode --
# "dynamic_ev" only makes sense for a strategy with its own continuous
# per-bar EV series; the threshold-based ModelDrivenStrategy policies
# cannot use it and are untouched.
EV_EXIT_MODES = ("fixed_r", "horizon_time", "dynamic_ev")


def dynamic_ev_exit(position: Position, bar: Bar, current_ev: float) -> ExitDecision:
    """Same protective ATR stop as every other exit in this codebase
    (checked first, intrabar -- legitimately real-time). Otherwise, holds
    while `current_ev` still favors the position's direction, and exits in
    full the moment it no longer does -- but **causally**: `current_ev` is
    only knowable once this bar has fully closed (it comes from the
    walk-forward model's OOS prediction as of this bar's close), so the
    decision is made now but the fill happens on the *next* bar's open,
    not this bar's own (already-past-tradable) close. `position.
    strategy_state["ev_exit_pending"]` carries that one-bar-deferred
    decision forward (a fresh `Position` gets a fresh empty dict per trade,
    so there's no leakage between positions).
    """
    is_long = position.direction == "long"
    stop_hit = bar.low <= position.stop_price if is_long else bar.high >= position.stop_price
    if stop_hit:
        return ExitDecision(action="stop", fill_price=position.stop_price, fraction=1.0)

    if position.strategy_state.get("ev_exit_pending"):
        return ExitDecision(action="ev_reversal", fill_price=bar.open, fraction=1.0)

    if pd.notna(current_ev):
        ev_favorable = current_ev > 0 if is_long else current_ev < 0
        if not ev_favorable:
            position.strategy_state["ev_exit_pending"] = True

    return ExitDecision(action="none")


@dataclass(frozen=True)
class EVSingleHorizonConfig:
    entry_timeframe: str = "5m"
    exit: HypothesisExitConfig = field(default_factory=HypothesisExitConfig)
    horizon: int = DEFAULT_HORIZON
    feature_cfg: FeatureConfig = field(default_factory=lambda: DEFAULT_FEATURE_CONFIG)
    n_folds: int = 8
    min_train_fraction: float = 0.2
    signal_selector: str = "rising_edge"
    debounce_bars: int = DEFAULT_DEBOUNCE_BARS
    exit_mode: str = "fixed_r"
    # Mirrors whatever --commission-per-contract/--slippage-ticks the
    # backtest itself is run with, so the entry decision's cost hurdle and
    # the engine's actual P&L impact always use the same cost assumption
    # (zero-cost runs pass 0.0/0.0, matching every other cost comparison
    # in this project).
    commission_per_contract: float = 0.0
    slippage_ticks: float = 0.0


class EVSingleHorizonStrategy(SignalCalendarMixin, HypothesisStrategy):
    """`timeframes`/entry sizing are inherited unchanged from
    `HypothesisStrategy`; calendar-lookup/exit-dispatch are inherited
    unchanged from `SignalCalendarMixin` (shared with `ModelDrivenStrategy`).
    """

    def __init__(self, cfg: EVSingleHorizonConfig, account: AccountConfig):
        self.cfg = cfg
        super().__init__(
            cfg.exit, account, entry_timeframe=cfg.entry_timeframe,
            warmup_bars=cfg.feature_cfg.volatility_lookback_bars,
        )
        self._ev: Optional[pd.Series] = None
        self._variance: Optional[pd.Series] = None
        self._cost_hurdle: Optional[pd.Series] = None
        self._signal_calendar: Optional[pd.DataFrame] = None
        self._last_owning_horizon: Optional[int] = None

    @property
    def name(self) -> str:
        return "ev_single_horizon"

    def on_precompute(self, full_history: dict) -> None:
        h = self.cfg.horizon
        features = build_feature_matrix(full_history, self.account, self.cfg.feature_cfg)
        atr_series = atr(self.bars_entry, period=self.cfg.feature_cfg.atr_period)
        labels = build_return_bin_labels(self.bars_entry, atr_series, horizons=(h,))[h]
        continuous_return = forward_return_atr(self.bars_entry["close"], atr_series, h)

        wf = walk_forward_predict(
            features, labels, n_folds=self.cfg.n_folds, min_train_fraction=self.cfg.min_train_fraction
        )
        ev_df = causal_expected_value(wf, labels, continuous_return)
        self._ev = ev_df["ev"]
        self._variance = ev_df["variance"]

        round_trip_cost_dollars = (
            self.cfg.commission_per_contract
            + 2 * self.cfg.slippage_ticks * self.account.contract.tick_size * self.account.contract.point_value
        )
        atr_dollars = atr_series * self.account.contract.point_value
        self._cost_hurdle = round_trip_cost_dollars / atr_dollars.where(atr_dollars > 0)

        self._signal_calendar = self._build_signal_calendar()

    def _build_raw_signal_series(self) -> pd.DataFrame:
        """Per bar: direction = sign(EV) if |EV| clears the current cost
        hurdle, else 0 (stand aside); strength = |EV|, used only to pick
        the strongest bar within a debounced streak (a single horizon's
        own EV series is only ever compared to itself here -- no
        cross-horizon comparability concern, unlike the threshold policies'
        confidence-percentile fix).
        """
        index = self.bars_entry.index
        ev, hurdle = self._ev, self._cost_hurdle
        valid = ev.notna() & hurdle.notna()

        direction = pd.Series(np.nan, index=index)
        direction.loc[valid] = 0.0
        direction.loc[valid & (ev > hurdle)] = 1.0
        direction.loc[valid & (ev < -hurdle)] = -1.0

        strength = pd.Series(np.nan, index=index)
        strength.loc[valid] = ev.loc[valid].abs()

        owning_horizon = pd.Series(np.nan, index=index)
        owning_horizon.loc[valid] = self.cfg.horizon

        return pd.DataFrame({"direction": direction, "strength": strength, "owning_horizon": owning_horizon})

    def _build_signal_calendar(self) -> pd.DataFrame:
        raw = self._build_raw_signal_series()
        selector_fn = SIGNAL_SELECTORS[self.cfg.signal_selector]
        return selector_fn(raw, self.cfg.debounce_bars)

    def check_exit(self, snapshot: MarketSnapshot, position: Position, session_ending: bool) -> ExitDecision:
        if self.cfg.exit_mode == "dynamic_ev":
            j = snapshot.timeframes["entry"].pos
            bar = snapshot.timeframes["entry"].bar(0)
            return dynamic_ev_exit(position, bar, self._ev.iloc[j])
        return super().check_exit(snapshot, position, session_ending)

    def build_context(self, snapshot: MarketSnapshot, atr_val: float) -> dict:
        j = snapshot.timeframes["entry"].pos
        return {
            "owning_horizon": self._last_owning_horizon,
            "entry_bar_pos": j,
            "signal_selector": self.cfg.signal_selector,
            "ev": float(self._ev.iloc[j]),
            "variance": float(self._variance.iloc[j]),
            "cost_hurdle": float(self._cost_hurdle.iloc[j]) if pd.notna(self._cost_hurdle.iloc[j]) else None,
        }

    def diagnostic_dimensions(self) -> list:
        return []


def add_cli_arguments(parser) -> None:
    parser.add_argument("--ev-horizon", type=int, choices=list(HORIZON_CHOICES), default=DEFAULT_HORIZON)
    parser.add_argument("--ev-n-folds", type=int, default=8)
    parser.add_argument("--ev-min-train-fraction", type=float, default=0.2)
    parser.add_argument("--ev-signal-selector", choices=list(SIGNAL_SELECTORS), default="rising_edge")
    parser.add_argument("--ev-debounce-bars", type=int, default=DEFAULT_DEBOUNCE_BARS)
    parser.add_argument("--ev-exit-mode", choices=list(EV_EXIT_MODES), default="fixed_r")


def build_from_cli_args(args) -> EVSingleHorizonConfig:
    return EVSingleHorizonConfig(
        exit=exit_cfg_from_args(args),
        horizon=args.ev_horizon,
        n_folds=args.ev_n_folds,
        min_train_fraction=args.ev_min_train_fraction,
        signal_selector=args.ev_signal_selector,
        debounce_bars=args.ev_debounce_bars,
        exit_mode=args.ev_exit_mode,
        commission_per_contract=args.commission_per_contract,
        slippage_ticks=args.slippage_ticks,
    )
