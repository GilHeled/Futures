"""
Decision policy: "full agreement" -- enter only if all of the 10/20/40-bar
predictive model's horizons imply the same non-flat direction, AND each
one individually clears its own confidence threshold. The conservative
baseline: fewer trades, but every one has three independent,
already-validated signals pointing the same way.
"""

from __future__ import annotations

from typing import Optional

from mnq_system.strategies.model_driven.base import (
    ModelDrivenConfig,
    ModelDrivenStrategy,
    add_shared_model_cli_arguments,
    base_cfg_from_args,
)

__all__ = ["ModelDrivenConfig", "FullAgreementStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args"]

DEFAULT_CONFIG = ModelDrivenConfig()


class FullAgreementStrategy(ModelDrivenStrategy):
    @property
    def name(self) -> str:
        return "model_full_agreement"

    def combine_horizon_signals(self, horizon_directions: dict, horizon_confidences: dict) -> Optional[str]:
        directions = set(horizon_directions.values())
        if len(directions) != 1:
            return None  # disagreement -- stand aside
        direction_val = directions.pop()
        if direction_val == 0:
            return None  # unanimous "flat" is not a directional call

        for confidence in horizon_confidences.values():
            if confidence < self.cfg.confidence_threshold:
                return None  # every horizon must individually clear the shared percentile bar

        return "long" if direction_val == 1 else "short"


def add_cli_arguments(parser) -> None:
    add_shared_model_cli_arguments(parser)


def build_from_cli_args(args) -> ModelDrivenConfig:
    return base_cfg_from_args(args)
