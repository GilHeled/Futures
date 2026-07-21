"""
Configuration for NaiveBenchmarkStrategy -- see strategy.py for why these
baselines share ORBStrategy's entry-time/ATR-stop/target-R shape rather
than inventing their own.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkConfig:
    entry_timeframe: str = "5m"
    # ET; defaults to ORBConfig.or_end so a benchmark run is directly
    # comparable to ORB -- same time of day, same trade frequency.
    entry_time: tuple = (10, 0)
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    target_r_multiple: float = 1.5
    direction: str = "long"  # "long" | "short" | "random"
    random_seed: int = 42  # deterministic per-day random draws, reproducible across reruns


DEFAULT_CONFIG = BenchmarkConfig()
