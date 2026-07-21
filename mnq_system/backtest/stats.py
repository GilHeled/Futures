"""
Statistical summary of a backtest run, following
references/verification-methodology.md section 4 ("report win rate, avg R,
max drawdown, and trade count together -- win rate alone is close to
meaningless without the other three").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from mnq_system.backtest.engine import BacktestResult, TradeRecord

TRADING_DAYS_PER_YEAR = 252
SMALL_SAMPLE_THRESHOLD = 30
DEFAULT_BOOTSTRAP_RESAMPLES = 2000
DEFAULT_BOOTSTRAP_CI = 0.90
DEFAULT_BOOTSTRAP_SEED = 42


@dataclass
class BacktestStats:
    total_trades: int
    win_rate: float
    avg_r_multiple: float
    profit_factor: float
    total_pnl: float
    total_return_pct: float
    starting_equity: float
    final_equity: float
    max_drawdown_pct: float
    max_drawdown_dollars: float
    sharpe_like: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_trade_duration: pd.Timedelta
    max_consecutive_wins: int
    max_consecutive_losses: int
    trades_by_setup: dict
    trades_by_exit_reason: dict
    small_sample_warning: bool

    def summary_text(self) -> str:
        lines = [
            "=== Backtest statistics ===",
            f"Trades:                {self.total_trades}"
            + ("  ** SMALL SAMPLE -- treat all figures below as unverified **" if self.small_sample_warning else ""),
            f"Win rate:               {self.win_rate:.1%}",
            f"Avg R multiple:         {self.avg_r_multiple:+.2f}R",
            f"Profit factor:          {self.profit_factor:.2f}",
            f"Total P&L:              ${self.total_pnl:,.2f}",
            f"Total return:           {self.total_return_pct:+.2%}  (${self.starting_equity:,.0f} -> ${self.final_equity:,.0f})",
            f"Max drawdown:           {self.max_drawdown_pct:.2%}  (${self.max_drawdown_dollars:,.2f})",
            f"Sharpe-like (ann., not rf-adjusted): {self.sharpe_like:.2f}",
            f"Avg win / avg loss:     ${self.avg_win:,.2f} / ${self.avg_loss:,.2f}",
            f"Largest win / loss:     ${self.largest_win:,.2f} / ${self.largest_loss:,.2f}",
            f"Avg trade duration:     {self.avg_trade_duration}",
            f"Max consecutive W/L:    {self.max_consecutive_wins} / {self.max_consecutive_losses}",
            f"Trades by setup type:   {dict(self.trades_by_setup)}",
            f"Trades by exit reason:  {dict(self.trades_by_exit_reason)}",
        ]
        if self.small_sample_warning:
            lines.append(
                f"\nWARNING: fewer than {SMALL_SAMPLE_THRESHOLD} trades. Per "
                "verification-methodology.md, a strategy with a thin trade count "
                "has NOT been verified regardless of how good these numbers look -- "
                "widen the date range before trusting this result."
            )
        return "\n".join(lines)


def _max_streak(flags: list[bool]) -> int:
    best = current = 0
    for f in flags:
        current = current + 1 if f else 0
        best = max(best, current)
    return best


def compute_stats(
    result: BacktestResult, starting_equity: float, small_sample_threshold: int = SMALL_SAMPLE_THRESHOLD
) -> BacktestStats:
    trades: list[TradeRecord] = result.trades
    n = len(trades)
    final_equity = result.final_equity

    if n == 0:
        empty_duration = pd.Timedelta(0)
        return BacktestStats(
            total_trades=0,
            win_rate=0.0,
            avg_r_multiple=0.0,
            profit_factor=0.0,
            total_pnl=final_equity - starting_equity,
            total_return_pct=(final_equity - starting_equity) / starting_equity,
            starting_equity=starting_equity,
            final_equity=final_equity,
            max_drawdown_pct=_max_drawdown_pct(result.equity_curve),
            max_drawdown_dollars=_max_drawdown_dollars(result.equity_curve),
            sharpe_like=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            largest_win=0.0,
            largest_loss=0.0,
            avg_trade_duration=empty_duration,
            max_consecutive_wins=0,
            max_consecutive_losses=0,
            trades_by_setup={},
            trades_by_exit_reason={},
            small_sample_warning=True,
        )

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    r_multiples = [t.r_multiple for t in trades]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    durations = [t.exit_time - t.entry_time for t in trades]

    return BacktestStats(
        total_trades=n,
        win_rate=len(wins) / n,
        avg_r_multiple=float(np.mean(r_multiples)),
        profit_factor=profit_factor,
        total_pnl=final_equity - starting_equity,
        total_return_pct=(final_equity - starting_equity) / starting_equity,
        starting_equity=starting_equity,
        final_equity=final_equity,
        max_drawdown_pct=_max_drawdown_pct(result.equity_curve),
        max_drawdown_dollars=_max_drawdown_dollars(result.equity_curve),
        sharpe_like=_sharpe_like(result.equity_curve),
        avg_win=float(np.mean(wins)) if wins else 0.0,
        avg_loss=float(np.mean(losses)) if losses else 0.0,
        largest_win=max(pnls),
        largest_loss=min(pnls),
        avg_trade_duration=pd.Series(durations).mean(),
        max_consecutive_wins=_max_streak([p > 0 for p in pnls]),
        max_consecutive_losses=_max_streak([p <= 0 for p in pnls]),
        trades_by_setup=dict(Counter(t.setup_type for t in trades)),
        trades_by_exit_reason=dict(Counter(t.exit_reason for t in trades)),
        small_sample_warning=n < small_sample_threshold,
    )


def _max_drawdown_pct(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    return float(drawdown.min())


def _max_drawdown_dollars(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = equity_curve - running_max
    return float(drawdown.min())


def _sharpe_like(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    daily = equity_curve.resample("1D").last().dropna()
    daily_returns = daily.pct_change().dropna()
    if len(daily_returns) < 2 or daily_returns.std() == 0:
        return 0.0
    return float(daily_returns.mean() / daily_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))


def trades_to_dataframe(trades: list[TradeRecord]) -> pd.DataFrame:
    """One row per trade, core fields plus every key present in any trade's
    `context` dict (see BacktestEngine._build_context) flattened into its
    own column -- this is the "detailed report for every trade" used to
    look for behavioral filters via `breakdown_by`.
    """
    rows = []
    for t in trades:
        row = {
            "setup_type": t.setup_type,
            "direction": t.direction,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "contracts": t.contracts,
            "pnl": t.pnl,
            "r_multiple": t.r_multiple,
            "exit_reason": t.exit_reason,
        }
        row.update(t.context)
        rows.append(row)
    return pd.DataFrame(rows)


def bootstrap_confidence(
    values,
    n_resamples: int = DEFAULT_BOOTSTRAP_RESAMPLES,
    ci: float = DEFAULT_BOOTSTRAP_CI,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict:
    """Bootstrap confidence interval on the mean of `values` (e.g. one
    bucket's R multiples), plus the fraction of bootstrap resamples whose
    mean is <= 0. That fraction is NOT a frequentist p-value -- it's a
    bootstrap-based read of "how often would this same sample, resampled
    with replacement, fail to show a positive edge." A CI that comfortably
    excludes zero is the strongest evidence available here that a bucket's
    edge isn't just small-sample noise; a CI straddling zero means don't
    trust the point estimate. Deterministic (fixed seed) for reproducibility.
    """
    clean = np.array([v for v in values if v is not None and pd.notna(v)], dtype=float)
    n = len(clean)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "prob_mean_le_zero": float("nan")}
    if n == 1:
        only = float(clean[0])
        return {"n": 1, "mean": only, "ci_low": only, "ci_high": only, "prob_mean_le_zero": float(only <= 0)}

    rng = np.random.default_rng(seed)
    resample_idx = rng.integers(0, n, size=(n_resamples, n))
    resample_means = clean[resample_idx].mean(axis=1)

    alpha = (1 - ci) / 2
    return {
        "n": n,
        "mean": float(clean.mean()),
        "ci_low": float(np.quantile(resample_means, alpha)),
        "ci_high": float(np.quantile(resample_means, 1 - alpha)),
        "prob_mean_le_zero": float((resample_means <= 0).mean()),
    }


def bootstrap_confidence_for_trades(trades: list[TradeRecord], **kwargs) -> dict:
    """`bootstrap_confidence` on a list of TradeRecords' R multiples."""
    return bootstrap_confidence([t.r_multiple for t in trades], **kwargs)


def breakdown_by(trades: list[TradeRecord], context_key: str) -> pd.DataFrame:
    """Win rate / avg R / profit factor / total P&L, PLUS a bootstrap 90% CI
    on avg R and P(edge <= 0), grouped by one context dimension (e.g.
    "entry_weekday", "volatility_regime", "trend_regime", "entry_hour_et")
    or a top-level TradeRecord field ("direction", "setup_type"). This is
    the tool for finding behavioral filters -- a bucket with a much better
    avg R is only a real candidate if its CI excludes zero and P(edge<=0)
    is low; otherwise it's likely small-sample noise. Always check trade
    count too -- bootstrap CIs on <10 trades are wide regardless.
    """
    df = trades_to_dataframe(trades)
    columns = ["trades", "win_rate", "avg_r", "avg_r_ci_low", "avg_r_ci_high", "prob_edge_le_zero", "profit_factor", "total_pnl"]
    if df.empty or context_key not in df.columns:
        return pd.DataFrame(columns=columns)

    def _agg(group: pd.DataFrame) -> pd.Series:
        wins = group.loc[group["pnl"] > 0, "pnl"]
        losses = group.loc[group["pnl"] <= 0, "pnl"]
        gross_loss = abs(losses.sum())
        boot = bootstrap_confidence(group["r_multiple"].tolist())
        return pd.Series(
            {
                "trades": len(group),
                "win_rate": (group["pnl"] > 0).mean(),
                "avg_r": group["r_multiple"].mean(),
                "avg_r_ci_low": boot["ci_low"],
                "avg_r_ci_high": boot["ci_high"],
                "prob_edge_le_zero": boot["prob_mean_le_zero"],
                "profit_factor": (wins.sum() / gross_loss) if gross_loss > 0 else float("inf"),
                "total_pnl": group["pnl"].sum(),
            }
        )

    return df.groupby(context_key, dropna=False).apply(_agg).sort_values("avg_r", ascending=False)


UNIVERSAL_BREAKDOWN_DIMENSIONS = ["direction", "setup_type", "entry_weekday", "entry_hour_et"]


def full_breakdown_report(trades: list[TradeRecord], dimensions: Optional[list] = None) -> dict:
    """Breakdown tables across a set of context dimensions. Defaults to the
    engine-universal dimensions every strategy's trades carry
    (`UNIVERSAL_BREAKDOWN_DIMENSIONS`) -- pass a strategy's own
    `diagnostic_dimensions()` (see mnq_system/strategy_api.py) to also break
    down its strategy-specific context fields (e.g. "bias", "volatility_regime").
    """
    if dimensions is None:
        dimensions = UNIVERSAL_BREAKDOWN_DIMENSIONS
    return {dim: breakdown_by(trades, dim) for dim in dimensions}


def equal_time_windows(start: pd.Timestamp, end: pd.Timestamp, n_windows: int) -> list:
    """`n_windows` + 1 boundaries splitting [start, end) into `n_windows`
    equal-length, non-overlapping chronological chunks.
    """
    total = end - start
    return [start + total * i / n_windows for i in range(n_windows + 1)]


def walk_forward_windows(trades: list[TradeRecord], window_boundaries: list) -> pd.DataFrame:
    """Slice `trades` into `len(window_boundaries) - 1` non-overlapping,
    chronologically independent windows and report each window's trade
    count, win rate, avg R (with bootstrap 90% CI), profit factor, and
    total P&L. This is the robustness check the single in-sample/
    out-of-sample split can't give you: a filter that only "works" in one
    period and falls apart in the others is not a robust edge, even if its
    combined-sample numbers look good.
    """
    rows = []
    for i in range(len(window_boundaries) - 1):
        lo, hi = window_boundaries[i], window_boundaries[i + 1]
        window_trades = [t for t in trades if lo <= t.entry_time < hi]
        n = len(window_trades)
        if n == 0:
            rows.append(
                {
                    "window_start": lo, "window_end": hi, "trades": 0, "win_rate": float("nan"),
                    "avg_r": float("nan"), "avg_r_ci_low": float("nan"), "avg_r_ci_high": float("nan"),
                    "profit_factor": float("nan"), "total_pnl": 0.0,
                }
            )
            continue
        pnls = [t.pnl for t in window_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_loss = abs(sum(losses))
        boot = bootstrap_confidence_for_trades(window_trades)
        rows.append(
            {
                "window_start": lo, "window_end": hi, "trades": n,
                "win_rate": len(wins) / n,
                "avg_r": boot["mean"],
                "avg_r_ci_low": boot["ci_low"], "avg_r_ci_high": boot["ci_high"],
                "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else float("inf"),
                "total_pnl": sum(pnls),
            }
        )
    return pd.DataFrame(rows)


def walk_forward_consistency(window_table: pd.DataFrame) -> dict:
    """Summary of how consistent a filter's edge is across the windows in
    `walk_forward_windows`'s output -- a high mean profit factor driven by
    one great window and several bad ones is a fragile edge, not a robust one.
    """
    valid = window_table.dropna(subset=["profit_factor"])
    valid = valid[np.isfinite(valid["profit_factor"].astype(float))]
    if valid.empty:
        return {"windows": 0, "frac_windows_pf_above_1": float("nan"), "mean_pf": float("nan"), "std_pf": float("nan"), "mean_avg_r": float("nan")}
    return {
        "windows": int(len(valid)),
        "frac_windows_pf_above_1": float((valid["profit_factor"] > 1.0).mean()),
        "mean_pf": float(valid["profit_factor"].mean()),
        "std_pf": float(valid["profit_factor"].std()),
        "mean_avg_r": float(valid["avg_r"].mean()),
    }


def split_in_sample_out_of_sample(
    result: BacktestResult, starting_equity: float, split_time: pd.Timestamp
) -> tuple[BacktestStats, BacktestStats]:
    """Report in-sample (before split_time) and out-of-sample (>= split_time)
    stats separately, per verification-methodology.md step 2. The
    out-of-sample number -- not the in-sample one -- is the honest estimate
    of how the rule set performs.
    """
    in_sample_trades = [t for t in result.trades if t.entry_time < split_time]
    oos_trades = [t for t in result.trades if t.entry_time >= split_time]

    in_sample_equity = result.equity_curve[result.equity_curve.index < split_time]
    oos_equity = result.equity_curve[result.equity_curve.index >= split_time]

    in_sample_final = in_sample_equity.iloc[-1] if not in_sample_equity.empty else starting_equity
    oos_start_equity = in_sample_final

    in_sample_result = BacktestResult(trades=in_sample_trades, equity_curve=in_sample_equity, final_equity=in_sample_final)
    oos_result = BacktestResult(
        trades=oos_trades,
        equity_curve=oos_equity,
        final_equity=oos_equity.iloc[-1] if not oos_equity.empty else oos_start_equity,
    )

    return (
        compute_stats(in_sample_result, starting_equity),
        compute_stats(oos_result, oos_start_equity),
    )
