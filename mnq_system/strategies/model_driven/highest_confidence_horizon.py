"""
Decision policy: "highest confidence horizon" -- each bar, look only at
whichever of the 10/20/40-bar horizons has the single highest confidence
right now, and trade its direction if it clears that horizon's own
threshold. Ignores the other two horizons entirely, even if they disagree.
"""

from __future__ import annotations

from typing import Optional

from mnq_system.strategies.common import noop_add_cli_arguments
from mnq_system.strategies.model_driven.base import ModelDrivenConfig, ModelDrivenStrategy, base_cfg_from_args

__all__ = [
    "ModelDrivenConfig", "HighestConfidenceHorizonStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args",
]

DEFAULT_CONFIG = ModelDrivenConfig()


class HighestConfidenceHorizonStrategy(ModelDrivenStrategy):
    @property
    def name(self) -> str:
        return "model_highest_confidence"

    def combine_horizon_signals(self, horizon_directions: dict, horizon_confidences: dict) -> Optional[str]:
        best_h = max(horizon_confidences, key=horizon_confidences.get)
        direction_val = horizon_directions[best_h]
        if direction_val == 0:
            return None
        if horizon_confidences[best_h] < self.cfg.confidence_threshold:
            return None
        return "long" if direction_val == 1 else "short"


add_cli_arguments = noop_add_cli_arguments  # shared --model-*/--hyp-* flags are registered by full_agreement's spec


def build_from_cli_args(args) -> ModelDrivenConfig:
    return base_cfg_from_args(args)
