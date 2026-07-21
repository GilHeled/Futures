"""
Command-line entry point.

    python -m mnq_system backtest --provider yfinance --start 2026-05-01 --end 2026-07-01
    python -m mnq_system backtest --provider databento --strategy ema_fib_reversal \\
        --start 2025-07-01 --end 2026-07-01 --output-dir ./out --oos-split 0.3

Run `python -m mnq_system backtest --help` for the full option list.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from mnq_system.backtest.engine import BacktestEngine, BacktestSettings
from mnq_system.backtest.stats import (
    UNIVERSAL_BREAKDOWN_DIMENSIONS,
    bootstrap_confidence_for_trades,
    compute_stats,
    equal_time_windows,
    full_breakdown_report,
    split_in_sample_out_of_sample,
    trades_to_dataframe,
    walk_forward_consistency,
    walk_forward_windows,
)
from mnq_system.config import CONTRACT_SPECS, DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import DataProviderError, build_provider
from mnq_system.indicators import atr
from mnq_system.modeling.evaluate import evaluate_all_horizons
from mnq_system.modeling.features import DEFAULT_FEATURE_CONFIG, build_feature_matrix
from mnq_system.modeling.labels import build_return_bin_labels, forward_return_atr
from mnq_system.replay import run_replay
from mnq_system.signal_audit import audit_log_to_dataframe
from mnq_system.strategies import STRATEGY_REGISTRY, get_strategy_spec

DEFAULT_SYMBOLS = {"yfinance": "MNQ=F", "databento": "MNQ", "massive": "MNQ", "csv": "MNQ"}
WARMUP_DAYS = 5  # extra history fetched before --start so a strategy's own indicators are warmed up


def _add_provider_arguments(sub_parser: argparse.ArgumentParser) -> None:
    """Flags for fetching historical bars from a DataProvider -- shared by
    every subcommand, including model-eval, which doesn't use a Strategy
    at all.
    """
    sub_parser.add_argument("--provider", choices=["yfinance", "databento", "massive", "csv"], default="yfinance")
    sub_parser.add_argument(
        "--symbol", default=None, help="Defaults per-provider (MNQ=F for yfinance, MNQ for databento/massive/csv)"
    )
    sub_parser.add_argument("--csv-path", default=None, help="Required when --provider csv")
    sub_parser.add_argument("--databento-api-key", default=None, help="Overrides DATABENTO_API_KEY env var")
    sub_parser.add_argument("--massive-api-key", default=None, help="Overrides MASSIVE_API_KEY env var")
    sub_parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable the local on-disk bar cache (on by default for databento/massive -- re-running the same "
             "range would otherwise re-fetch, and for Databento re-bill, the same data)",
    )
    sub_parser.add_argument("--start", required=True, help="YYYY-MM-DD, UTC")
    sub_parser.add_argument("--end", required=True, help="YYYY-MM-DD, UTC (exclusive)")


def _add_common_run_arguments(sub_parser: argparse.ArgumentParser) -> None:
    """Flags shared by `backtest` and `replay` -- both run a Strategy over a
    historical bar range, differing only in what they do with the decisions.
    """
    _add_provider_arguments(sub_parser)
    sub_parser.add_argument("--strategy", choices=list(STRATEGY_REGISTRY), default="ema_fib_reversal")
    sub_parser.add_argument("--account-equity", type=float, default=50_000.0)
    sub_parser.add_argument(
        "--risk-pct", type=float, default=None, help="Overrides account default risk %% per trade (e.g. 0.005)"
    )
    sub_parser.add_argument("--commission-per-contract", type=float, default=0.0)
    sub_parser.add_argument("--slippage-ticks", type=float, default=0.0)
    sub_parser.add_argument("--output-dir", default=None, help="Directory to write trades.csv and equity_curve.csv")

    # Each registered strategy contributes its own flags onto the shared
    # parser -- simple while there's a small number of strategies; revisit
    # if two strategies ever want the same flag name for different things.
    for spec in STRATEGY_REGISTRY.values():
        spec.add_cli_arguments(sub_parser)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mnq_system", description="Pluggable futures day-trading research platform")
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="Run the backtest over a historical date range")
    _add_common_run_arguments(bt)
    bt.add_argument("--oos-split", type=float, default=None, help="e.g. 0.3 reports the last 30%% of the range as out-of-sample")
    bt.add_argument(
        "--walk-forward-windows", type=int, default=None,
        help="e.g. 4 splits --start/--end into N equal, non-overlapping chronological windows and reports each independently",
    )

    rp = sub.add_parser(
        "replay",
        help="Replay historical bars one by one through the same Strategy interface a live loop would use, "
             "recording a complete signal audit log (audit_log.csv) of every entry/exit recommendation",
    )
    _add_common_run_arguments(rp)

    me = sub.add_parser(
        "model-eval",
        help="Walk-forward-evaluate a market-state predictive model (probability distribution of forward returns) "
             "against a naive chance baseline -- alpha discovery via predictive modeling, not a Strategy/trades",
    )
    _add_provider_arguments(me)
    me.add_argument("--interval", default="5m")
    me.add_argument("--horizons", default="5,10,20,40", help="Comma-separated forward-return horizons, in bars")
    me.add_argument("--n-folds", type=int, default=8)
    me.add_argument("--output-dir", default=None, help="Directory to write per-horizon fold/calibration CSVs")

    return parser


def _resolve_symbol(args) -> str:
    return args.symbol or DEFAULT_SYMBOLS[args.provider]


def _resolve_contract_spec(symbol: str):
    """Strips a provider-specific suffix (e.g. yfinance's "MNQ=F") before
    looking up CONTRACT_SPECS -- falls back to the existing default
    ContractSpec() (MNQ's own values) for anything unrecognized, so an
    unknown symbol behaves exactly as it did before this lookup existed.
    """
    base_symbol = symbol.split("=")[0].upper()
    return CONTRACT_SPECS.get(base_symbol, DEFAULT_ACCOUNT_CONFIG.contract)


def _build_account_config(args):
    account = replace(DEFAULT_ACCOUNT_CONFIG, contract=_resolve_contract_spec(_resolve_symbol(args)))
    if args.risk_pct is not None:
        account = replace(account, risk=replace(account.risk, risk_pct_per_trade=args.risk_pct))
    return account


def _build_provider_from_args(args):
    provider_kwargs = {}
    if args.provider == "csv":
        if not args.csv_path:
            raise SystemExit("--csv-path is required when --provider csv")
        provider_kwargs["path"] = args.csv_path
    if args.provider == "databento" and args.databento_api_key:
        provider_kwargs["api_key"] = args.databento_api_key
    if args.provider == "massive" and args.massive_api_key:
        provider_kwargs["api_key"] = args.massive_api_key
    return build_provider(args.provider, cache=not args.no_cache, **provider_kwargs)


def _load_bars(args, strategy) -> dict:
    symbol = _resolve_symbol(args)
    provider = _build_provider_from_args(args)
    start = pd.Timestamp(args.start, tz="UTC") - pd.Timedelta(days=WARMUP_DAYS)
    end = pd.Timestamp(args.end, tz="UTC")

    bars_by_timeframe = {}
    for name, tf_spec in strategy.timeframes.items():
        print(f"Fetching {tf_spec.interval} ({name}) bars for {symbol} from {args.provider} ...")
        bars_by_timeframe[name] = provider.get_historical_bars(
            symbol, start.to_pydatetime(), end.to_pydatetime(), tf_spec.interval
        )
    return bars_by_timeframe


def _load_single_interval_bars(args, interval: str) -> pd.DataFrame:
    symbol = _resolve_symbol(args)
    provider = _build_provider_from_args(args)
    start = pd.Timestamp(args.start, tz="UTC") - pd.Timedelta(days=WARMUP_DAYS)
    end = pd.Timestamp(args.end, tz="UTC")
    print(f"Fetching {interval} bars for {symbol} from {args.provider} ...")
    return provider.get_historical_bars(symbol, start.to_pydatetime(), end.to_pydatetime(), interval)


def run_backtest(args) -> int:
    spec = get_strategy_spec(args.strategy)
    strategy_cfg = spec.build_from_cli_args(args)
    account = _build_account_config(args)
    strategy = spec.strategy_cls(strategy_cfg, account)

    try:
        bars_by_timeframe = _load_bars(args, strategy)
    except DataProviderError as exc:
        print(f"Data error: {exc}", file=sys.stderr)
        return 1

    settings = BacktestSettings(
        account_equity=args.account_equity,
        commission_per_contract=args.commission_per_contract,
        slippage_ticks=args.slippage_ticks,
    )

    bar_counts = ", ".join(f"{len(bars)} {name}" for name, bars in bars_by_timeframe.items())
    print(f"Running backtest ({args.strategy}): {bar_counts} ...")
    engine = BacktestEngine(bars_by_timeframe, strategy, account, settings)
    result = engine.run()

    dimensions = UNIVERSAL_BREAKDOWN_DIMENSIONS + strategy.diagnostic_dimensions()

    print()
    if args.oos_split:
        start_ts = pd.Timestamp(args.start, tz="UTC")
        end_ts = pd.Timestamp(args.end, tz="UTC")
        split_time = start_ts + (end_ts - start_ts) * (1 - args.oos_split)
        in_sample_stats, oos_stats = split_in_sample_out_of_sample(result, args.account_equity, split_time)
        print(f"--- IN-SAMPLE  (before {split_time.date()}) ---")
        print(in_sample_stats.summary_text())
        print(f"\n--- OUT-OF-SAMPLE (from {split_time.date()}, the honest estimate) ---")
        print(oos_stats.summary_text())
    else:
        stats = compute_stats(result, args.account_equity)
        print(stats.summary_text())

    if result.trades:
        overall_boot = bootstrap_confidence_for_trades(result.trades)
        print(
            f"\nOverall avg R 90% bootstrap CI: [{overall_boot['ci_low']:+.3f}, {overall_boot['ci_high']:+.3f}]"
            f"  (P(edge<=0) ~ {overall_boot['prob_mean_le_zero']:.1%}, n={overall_boot['n']})"
        )
        print("\n--- Behavioral-filter breakdown (win rate / avg R [90% CI] / profit factor / total P&L by dimension) ---")
        print("Read this as candidates to test, not conclusions -- a wide CI or high P(edge<=0) means don't trust the point estimate.")
        for dim, table in full_breakdown_report(result.trades, dimensions=dimensions).items():
            if table.empty:
                continue
            print(f"\n[{dim}]")
            print(table.to_string(float_format=lambda v: f"{v:,.3f}"))

    walk_forward_table = None
    if args.walk_forward_windows and result.trades:
        start_ts = pd.Timestamp(args.start, tz="UTC")
        end_ts = pd.Timestamp(args.end, tz="UTC")
        boundaries = equal_time_windows(start_ts, end_ts, args.walk_forward_windows)
        walk_forward_table = walk_forward_windows(result.trades, boundaries)
        consistency = walk_forward_consistency(walk_forward_table)
        print(f"\n--- Walk-forward: {args.walk_forward_windows} independent chronological windows ---")
        print(walk_forward_table.to_string(float_format=lambda v: f"{v:,.3f}"))
        print(
            f"\n{consistency['windows']} windows with trades: "
            f"{consistency['frac_windows_pf_above_1']:.0%} had PF>1.0, "
            f"mean PF={consistency['mean_pf']:.2f} (std={consistency['std_pf']:.2f}), "
            f"mean avg R={consistency['mean_avg_r']:+.3f}"
        )
        if consistency["windows"] < args.walk_forward_windows:
            print(
                f"NOTE: {args.walk_forward_windows - consistency['windows']} window(s) had zero trades -- "
                "too few to judge; widen the window or the date range."
            )

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        trades_to_dataframe(result.trades).to_csv(out_dir / "trades.csv", index=False)
        result.equity_curve.to_csv(out_dir / "equity_curve.csv", header=True)
        written = [out_dir / "trades.csv", out_dir / "equity_curve.csv"]
        for dim, table in full_breakdown_report(result.trades, dimensions=dimensions).items():
            if table.empty:
                continue
            path = out_dir / f"breakdown_{dim}.csv"
            table.to_csv(path)
            written.append(path)
        if walk_forward_table is not None:
            path = out_dir / "walk_forward.csv"
            walk_forward_table.to_csv(path, index=False)
            written.append(path)
        print(f"\nWrote: {', '.join(str(p) for p in written)}")

    return 0


def run_replay_command(args) -> int:
    spec = get_strategy_spec(args.strategy)
    strategy_cfg = spec.build_from_cli_args(args)
    account = _build_account_config(args)
    strategy = spec.strategy_cls(strategy_cfg, account)
    symbol = _resolve_symbol(args)

    if spec.status != "live_candidate":
        print(
            f"NOTE: '{args.strategy}' is marked {spec.status} -- not validated as a live candidate. "
            "This replay is for verifying the signal pipeline, not for trading decisions."
        )

    try:
        bars_by_timeframe = _load_bars(args, strategy)
    except DataProviderError as exc:
        print(f"Data error: {exc}", file=sys.stderr)
        return 1

    settings = BacktestSettings(
        account_equity=args.account_equity,
        commission_per_contract=args.commission_per_contract,
        slippage_ticks=args.slippage_ticks,
    )

    bar_counts = ", ".join(f"{len(bars)} {name}" for name, bars in bars_by_timeframe.items())
    print(f"Running replay ({args.strategy}): {bar_counts} ...")
    result = run_replay(bars_by_timeframe, strategy, account, settings, symbol)

    dimensions = UNIVERSAL_BREAKDOWN_DIMENSIONS + strategy.diagnostic_dimensions()
    print()
    stats = compute_stats(result, args.account_equity)
    print(stats.summary_text())
    print(f"\nSignal audit log: {len(result.audit_log)} entries")

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        trades_to_dataframe(result.trades).to_csv(out_dir / "trades.csv", index=False)
        result.equity_curve.to_csv(out_dir / "equity_curve.csv", header=True)
        audit_log_to_dataframe(result.audit_log).to_csv(out_dir / "audit_log.csv", index=False)
        written = [out_dir / "trades.csv", out_dir / "equity_curve.csv", out_dir / "audit_log.csv"]
        for dim, table in full_breakdown_report(result.trades, dimensions=dimensions).items():
            if table.empty:
                continue
            path = out_dir / f"breakdown_{dim}.csv"
            table.to_csv(path)
            written.append(path)
        print(f"\nWrote: {', '.join(str(p) for p in written)}")

    return 0


def run_model_eval(args) -> int:
    horizons = tuple(int(h) for h in args.horizons.split(","))
    account = DEFAULT_ACCOUNT_CONFIG  # only account.session.timezone is used (feature building, no position sizing)

    try:
        bars = _load_single_interval_bars(args, args.interval)
    except DataProviderError as exc:
        print(f"Data error: {exc}", file=sys.stderr)
        return 1

    print(f"Building feature matrix + labels ({len(bars)} bars, horizons={horizons}) ...")
    feature_cfg = DEFAULT_FEATURE_CONFIG
    features = build_feature_matrix({"entry": bars}, account, feature_cfg)
    atr_series = atr(bars, period=feature_cfg.atr_period)
    labels_by_horizon = build_return_bin_labels(bars, atr_series, horizons=horizons)
    # Continuous (pre-binning) ATR-normalized forward return per horizon --
    # used only for the confidence-decile "would this have been a good
    # trade" check, never as a feature or a training label.
    continuous_returns_by_horizon = {h: forward_return_atr(bars["close"], atr_series, h) for h in horizons}

    print(f"Running walk-forward evaluation ({args.n_folds} expanding folds per horizon) ...")
    results = evaluate_all_horizons(
        features, labels_by_horizon, n_folds=args.n_folds,
        continuous_returns_by_horizon=continuous_returns_by_horizon,
    )

    for h in horizons:
        print()
        print(results[h].summary_text())

    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for h in horizons:
            result = results[h]
            named_tables = [
                ("folds", result.folds),
                ("calibration", result.calibration),
                ("calibration_error", result.calibration_error),
                ("coefficient_stability", result.coefficient_stability),
                ("regime_breakdown", result.regime_breakdown),
                ("year_breakdown", result.year_breakdown),
                ("confidence_deciles", result.confidence_deciles),
                ("directional_confidence_deciles", result.directional_confidence_deciles),
            ]
            for name, table in named_tables:
                if table.empty:
                    continue
                path = out_dir / f"model_eval_h{h}_{name}.csv"
                table.to_csv(path, index=False)
                written.append(path)
        print(f"\nWrote: {', '.join(str(p) for p in written)}")

    return 0


def main(argv=None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "backtest":
        return run_backtest(args)
    if args.command == "replay":
        return run_replay_command(args)
    if args.command == "model-eval":
        return run_model_eval(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
