from dataclasses import replace

from mnq_system.strategies.orb.config import DEFAULT_CONFIG, ORBConfig
from mnq_system.strategies.orb.strategy import ORBStrategy

__all__ = ["ORBConfig", "ORBStrategy", "DEFAULT_CONFIG", "add_cli_arguments", "build_from_cli_args"]


def _parse_hhmm(value: str) -> tuple:
    h, m = value.split(":")
    return (int(h), int(m))


def add_cli_arguments(parser) -> None:
    """Registers this strategy's CLI flags onto an argparse (sub)parser."""
    parser.add_argument("--orb-or-start", default="09:30", help="Opening range start, HH:MM ET (inclusive)")
    parser.add_argument("--orb-or-end", default="10:00", help="Opening range end, HH:MM ET (exclusive)")
    parser.add_argument("--orb-cutoff", default="11:30", help="No new ORB entries at/after this time, HH:MM ET")
    parser.add_argument("--orb-target-r", type=float, default=DEFAULT_CONFIG.target_r_multiple)
    parser.add_argument("--orb-max-range-atr-mult", type=float, default=DEFAULT_CONFIG.max_range_atr_mult)
    parser.add_argument("--orb-stop-atr-mult", type=float, default=DEFAULT_CONFIG.stop_atr_mult)


def build_from_cli_args(args) -> ORBConfig:
    return replace(
        DEFAULT_CONFIG,
        or_start=_parse_hhmm(args.orb_or_start),
        or_end=_parse_hhmm(args.orb_or_end),
        entry_cutoff=_parse_hhmm(args.orb_cutoff),
        target_r_multiple=args.orb_target_r,
        max_range_atr_mult=args.orb_max_range_atr_mult,
        stop_atr_mult=args.orb_stop_atr_mult,
    )
