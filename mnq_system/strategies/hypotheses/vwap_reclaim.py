"""
Hypothesis: does a return to VWAP -- price having moved meaningfully away
from session VWAP, then coming back to touch it -- predict a bounce
continuation of the side it came from (VWAP acting as dynamic
support/resistance)? (The mirror "fade/reversal" version is a simple
direction flip, not built here.)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

import pandas as pd

from mnq_system.config import AccountConfig
from mnq_system.indicators import session_vwap
from mnq_system.strategies.hypotheses.base import (
    HypothesisExitConfig,
    HypothesisStrategy,
    exit_cfg_from_args,
)
from mnq_system.strategy_api import MarketSnapshot

__all__ = ["VwapReclaimConfig", "VwapReclaimStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args"]


@dataclass(frozen=True)
class VwapReclaimConfig:
    entry_timeframe: str = "5m"
    exit: HypothesisExitConfig = field(default_factory=HypothesisExitConfig)
    extension_atr_mult: float = 1.0  # how far from VWAP counts as "extended"
    touch_tolerance_atr_mult: float = 0.15  # how close counts as "touching" VWAP on the return


DEFAULT_CONFIG = VwapReclaimConfig()


class VwapReclaimStrategy(HypothesisStrategy):
    def __init__(self, cfg: VwapReclaimConfig, account: AccountConfig):
        self.cfg = cfg
        super().__init__(cfg.exit, account, entry_timeframe=cfg.entry_timeframe, warmup_bars=0)
        self.entry_vwap: Optional[pd.Series] = None
        self._extended_side: Optional[str] = None  # "above" | "below" | None
        self._pending_return: Optional[str] = None  # "long" | "short" | None -- queued by on_event_bar

    @property
    def name(self) -> str:
        return "vwap_reclaim"

    def on_precompute(self, full_history: dict) -> None:
        self.entry_vwap = session_vwap(self.bars_entry, tz=self.timezone)

    def on_event_bar(self, snapshot: MarketSnapshot) -> None:
        # Tracked every bar regardless of session-window gating or an open
        # position -- a touch that happens mid-trade (from a prior event)
        # must not be missed just because check_entry wasn't consulted.
        view = snapshot.timeframes["entry"]
        j = view.pos
        atr_val = self.entry_atr.iloc[j]
        vwap_val = self.entry_vwap.iloc[j]
        if pd.isna(atr_val) or atr_val <= 0 or pd.isna(vwap_val):
            return

        dist = view.bar(0).close - vwap_val
        tolerance = self.cfg.touch_tolerance_atr_mult * atr_val
        extension = self.cfg.extension_atr_mult * atr_val

        if self._extended_side == "above" and abs(dist) <= tolerance:
            self._pending_return = "long"  # was above, touched back down to VWAP -> bounce-continuation up
            self._extended_side = None
        elif self._extended_side == "below" and abs(dist) <= tolerance:
            self._pending_return = "short"
            self._extended_side = None
        elif dist > extension:
            self._extended_side = "above"
        elif dist < -extension:
            self._extended_side = "below"

    def detect_event(self, snapshot: MarketSnapshot) -> Optional[str]:
        if self._pending_return is None:
            return None
        direction = self._pending_return
        self._pending_return = None
        return direction

    def build_context(self, snapshot: MarketSnapshot, atr_val: float) -> dict:
        j = snapshot.timeframes["entry"].pos
        vwap_val = self.entry_vwap.iloc[j]
        return {"vwap": float(vwap_val) if pd.notna(vwap_val) else None}


def add_cli_arguments(parser) -> None:
    parser.add_argument("--vwap-extension-atr-mult", type=float, default=DEFAULT_CONFIG.extension_atr_mult)
    parser.add_argument("--vwap-touch-tolerance-atr-mult", type=float, default=DEFAULT_CONFIG.touch_tolerance_atr_mult)


def build_from_cli_args(args) -> VwapReclaimConfig:
    return replace(
        DEFAULT_CONFIG, exit=exit_cfg_from_args(args), extension_atr_mult=args.vwap_extension_atr_mult,
        touch_tolerance_atr_mult=args.vwap_touch_tolerance_atr_mult,
    )
