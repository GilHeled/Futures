from dataclasses import replace

from mnq_system.strategies.benchmarks.config import BenchmarkConfig, DEFAULT_CONFIG
from mnq_system.strategies.benchmarks.strategy import NaiveBenchmarkStrategy

__all__ = [
    "BenchmarkConfig", "NaiveBenchmarkStrategy", "DEFAULT_CONFIG",
    "add_cli_arguments", "noop_add_cli_arguments", "make_build_from_cli_args",
]


def _parse_hhmm(value: str) -> tuple:
    h, m = value.split(":")
    return (int(h), int(m))


def add_cli_arguments(parser) -> None:
    """Registers the flags shared by every benchmark_* registry entry.

    IMPORTANT: only ONE of the benchmark StrategySpecs should reference this
    function (see mnq_system/strategies/__init__.py) -- every registered
    strategy's add_cli_arguments gets called on the same shared parser, and
    argparse errors on a flag being added twice. The other benchmark specs
    use `noop_add_cli_arguments` instead.
    """
    parser.add_argument(
        "--benchmark-entry-time", default="10:00",
        help="Fixed daily entry time, HH:MM ET (defaults to ORB's opening-range close, for apples-to-apples comparison)",
    )
    parser.add_argument("--benchmark-atr-period", type=int, default=DEFAULT_CONFIG.atr_period)
    parser.add_argument("--benchmark-stop-atr-mult", type=float, default=DEFAULT_CONFIG.stop_atr_mult)
    parser.add_argument("--benchmark-target-r", type=float, default=DEFAULT_CONFIG.target_r_multiple)
    parser.add_argument("--benchmark-random-seed", type=int, default=DEFAULT_CONFIG.random_seed)


def noop_add_cli_arguments(parser) -> None:
    return


def make_build_from_cli_args(direction: str):
    """Returns a build_from_cli_args closure hardcoded to one direction --
    lets the three benchmark_* registry entries share one config/strategy
    class while each defaulting to a different naive rule.
    """

    def build_from_cli_args(args):
        return replace(
            DEFAULT_CONFIG,
            direction=direction,
            entry_time=_parse_hhmm(args.benchmark_entry_time),
            atr_period=args.benchmark_atr_period,
            stop_atr_mult=args.benchmark_stop_atr_mult,
            target_r_multiple=args.benchmark_target_r,
            random_seed=args.benchmark_random_seed,
        )

    return build_from_cli_args
