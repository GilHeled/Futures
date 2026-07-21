"""
Decision policy: "longest horizon only" -- the control. Only ever consults
the 40-bar horizon (the strongest, most decisive signal on its own per the
confidence-decile analysis); the 10- and 20-bar horizons are computed
(same on_precompute cost) but never consulted. Answers "do the shorter
horizons add anything over just using the most decisive one alone."
"""

from __future__ import annotations

from typing import Optional

from mnq_system.strategies.common import noop_add_cli_arguments
from mnq_system.strategies.model_driven.base import ModelDrivenConfig, ModelDrivenStrategy, base_cfg_from_args

__all__ = ["ModelDrivenConfig", "LongestHorizonOnlyStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args"]

DEFAULT_CONFIG = ModelDrivenConfig()


class LongestHorizonOnlyStrategy(ModelDrivenStrategy):
    @property
    def name(self) -> str:
        return "model_longest_horizon"

    def combine_horizon_signals(self, horizon_directions: dict, horizon_confidences: dict) -> Optional[str]:
        h = max(self.cfg.horizons)
        direction_val = horizon_directions[h]
        if direction_val == 0:
            return None
        if horizon_confidences[h] < self.cfg.confidence_threshold:
            return None
        return "long" if direction_val == 1 else "short"


add_cli_arguments = noop_add_cli_arguments  # shared --model-*/--hyp-* flags are registered by full_agreement's spec


def build_from_cli_args(args) -> ModelDrivenConfig:
    return base_cfg_from_args(args)
