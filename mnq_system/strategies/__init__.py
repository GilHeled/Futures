"""
Strategy registry: name -> StrategySpec. Mirrors
mnq_system.data.providers.build_provider's factory pattern -- add a new
strategy by adding one entry here, without touching cli.py or engine.py.

Every strategy's constructor takes `(cfg, account)` positionally, where
`cfg` is that strategy's own config (`spec.config_cls`) and `account` is a
shared `mnq_system.config.AccountConfig`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mnq_system.strategies.ema_fib_reversal import (
    EmaFibReversalConfig,
    EmaFibReversalStrategy,
)
from mnq_system.strategies.ema_fib_reversal import add_cli_arguments as _ema_fib_reversal_add_cli_arguments
from mnq_system.strategies.ema_fib_reversal import build_from_cli_args as _ema_fib_reversal_build_from_cli_args
from mnq_system.strategies.orb import ORBConfig, ORBStrategy
from mnq_system.strategies.orb import add_cli_arguments as _orb_add_cli_arguments
from mnq_system.strategies.orb import build_from_cli_args as _orb_build_from_cli_args
from mnq_system.strategies.benchmarks import BenchmarkConfig, NaiveBenchmarkStrategy
from mnq_system.strategies.benchmarks import add_cli_arguments as _benchmark_add_cli_arguments
from mnq_system.strategies.benchmarks import make_build_from_cli_args as _benchmark_build_from_cli_args
from mnq_system.strategies.benchmarks import noop_add_cli_arguments as _benchmark_noop_add_cli_arguments
from mnq_system.strategies.hypotheses.liquidity_sweep import LiquiditySweepConfig, LiquiditySweepStrategy
from mnq_system.strategies.hypotheses.liquidity_sweep import add_cli_arguments as _sweep_add_cli_arguments
from mnq_system.strategies.hypotheses.liquidity_sweep import build_from_cli_args as _sweep_build_from_cli_args
from mnq_system.strategies.hypotheses.opening_gap import OpeningGapConfig, OpeningGapStrategy
from mnq_system.strategies.hypotheses.opening_gap import add_cli_arguments as _gap_add_cli_arguments
from mnq_system.strategies.hypotheses.opening_gap import build_from_cli_args as _gap_build_from_cli_args
from mnq_system.strategies.hypotheses.vwap_reclaim import VwapReclaimConfig, VwapReclaimStrategy
from mnq_system.strategies.hypotheses.vwap_reclaim import add_cli_arguments as _vwap_add_cli_arguments
from mnq_system.strategies.hypotheses.vwap_reclaim import build_from_cli_args as _vwap_build_from_cli_args
from mnq_system.strategies.hypotheses.overnight_imbalance import OvernightImbalanceConfig, OvernightImbalanceStrategy
from mnq_system.strategies.hypotheses.overnight_imbalance import add_cli_arguments as _overnight_add_cli_arguments
from mnq_system.strategies.hypotheses.overnight_imbalance import build_from_cli_args as _overnight_build_from_cli_args
from mnq_system.strategies.hypotheses.pullback_continuation import (
    PullbackContinuationConfig,
    PullbackContinuationStrategy,
)
from mnq_system.strategies.hypotheses.pullback_continuation import add_cli_arguments as _pullback_add_cli_arguments
from mnq_system.strategies.hypotheses.pullback_continuation import build_from_cli_args as _pullback_build_from_cli_args
from mnq_system.strategies.model_driven.full_agreement import ModelDrivenConfig, FullAgreementStrategy
from mnq_system.strategies.model_driven.full_agreement import add_cli_arguments as _model_full_agreement_add_cli_arguments
from mnq_system.strategies.model_driven.full_agreement import build_from_cli_args as _model_full_agreement_build_from_cli_args
from mnq_system.strategies.model_driven.weighted_combination import WeightedCombinationConfig, WeightedCombinationStrategy
from mnq_system.strategies.model_driven.weighted_combination import add_cli_arguments as _model_weighted_add_cli_arguments
from mnq_system.strategies.model_driven.weighted_combination import build_from_cli_args as _model_weighted_build_from_cli_args
from mnq_system.strategies.model_driven.highest_confidence_horizon import (
    HighestConfidenceHorizonStrategy,
    add_cli_arguments as _model_highest_confidence_add_cli_arguments,
    build_from_cli_args as _model_highest_confidence_build_from_cli_args,
)
from mnq_system.strategies.model_driven.longest_horizon_only import (
    LongestHorizonOnlyStrategy,
    add_cli_arguments as _model_longest_horizon_add_cli_arguments,
    build_from_cli_args as _model_longest_horizon_build_from_cli_args,
)
from mnq_system.strategies.model_driven.ev_single_horizon import (
    EVSingleHorizonConfig,
    EVSingleHorizonStrategy,
    add_cli_arguments as _ev_single_horizon_add_cli_arguments,
    build_from_cli_args as _ev_single_horizon_build_from_cli_args,
)


@dataclass(frozen=True)
class StrategySpec:
    config_cls: type
    add_cli_arguments: Callable
    build_from_cli_args: Callable
    strategy_cls: type
    # "live_candidate": validated enough to run live/paper.
    # "regression_test": kept only to exercise the platform's own test/validation
    #   machinery -- must not be treated as a live candidate.
    # "experimental": under active investigation, no verdict yet.
    # "benchmark": a naive baseline (random/always-long/always-short) used
    #   to judge whether another strategy's numbers beat chance -- never a
    #   live candidate, and not itself "under investigation".
    # "hypothesis": an atomic market-hypothesis test (alpha discovery) --
    #   validated independently, before (if it survives) being combined
    #   with others into a complete strategy.
    # "experimental" (model_driven): a decision-level EV candidate built on
    #   a walk-forward-validated predictive model -- the combination policy
    #   itself is an untested hypothesis until this validation runs.
    status: str = "experimental"


STRATEGY_REGISTRY = {
    # No robust, persistent edge was demonstrated for this rule set over a
    # 7-year MNQ backtest (see the project memory of that investigation) --
    # it stays in the registry purely as a regression-test strategy for the
    # platform itself, not as a live candidate.
    "ema_fib_reversal": StrategySpec(
        config_cls=EmaFibReversalConfig,
        add_cli_arguments=_ema_fib_reversal_add_cli_arguments,
        build_from_cli_args=_ema_fib_reversal_build_from_cli_args,
        strategy_cls=EmaFibReversalStrategy,
        status="regression_test",
    ),
    # Structurally different from ema_fib_reversal: a plain opening-range
    # breakout with no bias filter, no Fibonacci zones, no candlestick
    # confirmation. Under active investigation -- no verdict yet.
    "orb": StrategySpec(
        config_cls=ORBConfig,
        add_cli_arguments=_orb_add_cli_arguments,
        build_from_cli_args=_orb_build_from_cli_args,
        strategy_cls=ORBStrategy,
        status="experimental",
    ),
    # Naive baselines sharing the same entry-time/ATR-stop/target-R shape as
    # ORB's dominant branch -- run alongside any real strategy on the same
    # data to judge whether its avg R/PF is actually meaningful or just what
    # chance (or a fixed directional bias) would also produce. Only
    # "benchmark_always_long" exposes the shared --benchmark-* CLI flags
    # (all three specs' add_cli_arguments run against the same parser --
    # registering the flags three times would collide).
    "benchmark_always_long": StrategySpec(
        config_cls=BenchmarkConfig,
        add_cli_arguments=_benchmark_add_cli_arguments,
        build_from_cli_args=_benchmark_build_from_cli_args("long"),
        strategy_cls=NaiveBenchmarkStrategy,
        status="benchmark",
    ),
    "benchmark_always_short": StrategySpec(
        config_cls=BenchmarkConfig,
        add_cli_arguments=_benchmark_noop_add_cli_arguments,
        build_from_cli_args=_benchmark_build_from_cli_args("short"),
        strategy_cls=NaiveBenchmarkStrategy,
        status="benchmark",
    ),
    "benchmark_random": StrategySpec(
        config_cls=BenchmarkConfig,
        add_cli_arguments=_benchmark_noop_add_cli_arguments,
        build_from_cli_args=_benchmark_build_from_cli_args("random"),
        strategy_cls=NaiveBenchmarkStrategy,
        status="benchmark",
    ),
    # Alpha discovery: atomic market-hypothesis tests, each a standardized
    # single-signal trade (ATR-stop, fixed R target -- see
    # mnq_system/strategies/hypotheses/base.py) so every hypothesis is
    # directly comparable to every other, to ORB, and to the benchmark_*
    # baselines on the same avg-R/PF scale. Each is validated independently;
    # only a survivor gets combined with others into a complete strategy.
    "liquidity_sweep": StrategySpec(
        config_cls=LiquiditySweepConfig,
        add_cli_arguments=_sweep_add_cli_arguments,
        build_from_cli_args=_sweep_build_from_cli_args,
        strategy_cls=LiquiditySweepStrategy,
        status="hypothesis",
    ),
    "opening_gap": StrategySpec(
        config_cls=OpeningGapConfig,
        add_cli_arguments=_gap_add_cli_arguments,
        build_from_cli_args=_gap_build_from_cli_args,
        strategy_cls=OpeningGapStrategy,
        status="hypothesis",
    ),
    "vwap_reclaim": StrategySpec(
        config_cls=VwapReclaimConfig,
        add_cli_arguments=_vwap_add_cli_arguments,
        build_from_cli_args=_vwap_build_from_cli_args,
        strategy_cls=VwapReclaimStrategy,
        status="hypothesis",
    ),
    "overnight_imbalance": StrategySpec(
        config_cls=OvernightImbalanceConfig,
        add_cli_arguments=_overnight_add_cli_arguments,
        build_from_cli_args=_overnight_build_from_cli_args,
        strategy_cls=OvernightImbalanceStrategy,
        status="hypothesis",
    ),
    "pullback_continuation": StrategySpec(
        config_cls=PullbackContinuationConfig,
        add_cli_arguments=_pullback_add_cli_arguments,
        build_from_cli_args=_pullback_build_from_cli_args,
        strategy_cls=PullbackContinuationStrategy,
        status="hypothesis",
    ),
    # Decision-level EV: candidate policies for combining the walk-forward-
    # validated 10/20/40-bar predictive model's horizons into a single
    # long/short/stand-aside decision, all sharing the same standardized
    # ATR-stop/R-target exit (mnq_system/strategies/model_driven/base.py) so
    # only the entry decision varies between them. Which policy (if any)
    # the data actually favors is exactly what this registry entry set
    # exists to find out -- none is assumed to be the answer going in.
    "model_full_agreement": StrategySpec(
        config_cls=ModelDrivenConfig,
        add_cli_arguments=_model_full_agreement_add_cli_arguments,
        build_from_cli_args=_model_full_agreement_build_from_cli_args,
        strategy_cls=FullAgreementStrategy,
        status="experimental",
    ),
    "model_weighted": StrategySpec(
        config_cls=WeightedCombinationConfig,
        add_cli_arguments=_model_weighted_add_cli_arguments,
        build_from_cli_args=_model_weighted_build_from_cli_args,
        strategy_cls=WeightedCombinationStrategy,
        status="experimental",
    ),
    "model_highest_confidence": StrategySpec(
        config_cls=ModelDrivenConfig,
        add_cli_arguments=_model_highest_confidence_add_cli_arguments,
        build_from_cli_args=_model_highest_confidence_build_from_cli_args,
        strategy_cls=HighestConfidenceHorizonStrategy,
        status="experimental",
    ),
    "model_longest_horizon": StrategySpec(
        config_cls=ModelDrivenConfig,
        add_cli_arguments=_model_longest_horizon_add_cli_arguments,
        build_from_cli_args=_model_longest_horizon_build_from_cli_args,
        strategy_cls=LongestHorizonOnlyStrategy,
        status="experimental",
    ),
    # Different research question from every model_* entry above: does an
    # expected-value decision rule on the model's FULL predicted
    # distribution (not a reduced top-1-class/confidence threshold) improve
    # decision quality, tested one horizon at a time (--ev-horizon), with NO
    # cross-horizon aggregation -- how to combine horizons is deliberately
    # deferred to its own future phase, not decided here.
    "ev_single_horizon": StrategySpec(
        config_cls=EVSingleHorizonConfig,
        add_cli_arguments=_ev_single_horizon_add_cli_arguments,
        build_from_cli_args=_ev_single_horizon_build_from_cli_args,
        strategy_cls=EVSingleHorizonStrategy,
        status="experimental",
    ),
}


def get_strategy_spec(name: str) -> StrategySpec:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(STRATEGY_REGISTRY)}")
    return STRATEGY_REGISTRY[name]
