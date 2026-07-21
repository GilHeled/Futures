"""
Decision policy: "weighted combination" -- composite score
sum(confidence_h * direction_h) across the 10/20/40-bar horizons; enters in
the sign of the composite if its magnitude clears a single combined
threshold. Captures partial agreement (e.g. two strong horizons outvoting
one weak dissenter) that "full agreement" would reject outright.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mnq_system.config import AccountConfig
from mnq_system.strategies.model_driven.base import ModelDrivenConfig, ModelDrivenStrategy, base_cfg_from_args

__all__ = [
    "WeightedCombinationConfig", "WeightedCombinationStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args",
]

DEFAULT_COMPOSITE_THRESHOLD = 0.30


@dataclass(frozen=True)
class WeightedCombinationConfig:
    base: ModelDrivenConfig = field(default_factory=ModelDrivenConfig)
    # |sum(confidence_h * direction_h)| must clear this to enter -- unlike
    # full_agreement's per-horizon thresholds, this is one combined bar on
    # the composite score itself.
    composite_threshold: float = DEFAULT_COMPOSITE_THRESHOLD


DEFAULT_CONFIG = WeightedCombinationConfig()


class WeightedCombinationStrategy(ModelDrivenStrategy):
    def __init__(self, cfg: WeightedCombinationConfig, account: AccountConfig):
        super().__init__(cfg.base, account)
        self.policy_cfg = cfg

    @property
    def name(self) -> str:
        return "model_weighted"

    def combine_horizon_signals(self, horizon_directions: dict, horizon_confidences: dict) -> Optional[str]:
        composite = sum(horizon_confidences[h] * horizon_directions[h] for h in horizon_directions)
        if abs(composite) < self.policy_cfg.composite_threshold:
            return None
        return "long" if composite > 0 else "short"


def add_cli_arguments(parser) -> None:
    parser.add_argument("--model-composite-threshold", type=float, default=DEFAULT_COMPOSITE_THRESHOLD)


def build_from_cli_args(args) -> WeightedCombinationConfig:
    return WeightedCombinationConfig(
        base=base_cfg_from_args(args), composite_threshold=args.model_composite_threshold
    )
