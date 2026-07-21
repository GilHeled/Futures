from dataclasses import replace

from mnq_system.strategies.ema_fib_reversal.config import DEFAULT_CONFIG, EmaFibReversalConfig
from mnq_system.strategies.ema_fib_reversal.strategy import EmaFibReversalStrategy

__all__ = ["EmaFibReversalConfig", "EmaFibReversalStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args"]


def add_cli_arguments(parser) -> None:
    """Registers this strategy's CLI flags onto an argparse (sub)parser."""
    parser.add_argument("--bias-tf", default=DEFAULT_CONFIG.bias_timeframe)
    parser.add_argument("--entry-tf", default=DEFAULT_CONFIG.entry_timeframe)
    parser.add_argument("--no-reversal", action="store_true", help="Disable reversal (change-of-character) entries")
    parser.add_argument("--no-pullback", action="store_true", help="Disable pullback (continuation) entries")
    parser.add_argument(
        "--filter-reversal-long-vs-bearish-bias",
        action="store_true",
        help="Block reversal-long entries while 15m bias reads bearish (empirically weak bucket under test)",
    )


def build_from_cli_args(args) -> EmaFibReversalConfig:
    return replace(
        DEFAULT_CONFIG,
        bias_timeframe=args.bias_tf,
        entry_timeframe=args.entry_tf,
        enable_reversal_entries=not args.no_reversal,
        enable_pullback_entries=not args.no_pullback,
        filter_reversal_long_vs_bearish_bias=args.filter_reversal_long_vs_bearish_bias,
    )
