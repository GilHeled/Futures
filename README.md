# MNQ Rule-Based Day-Trading System

A Python/CLI backtester (and, in a later phase, a signal-only live monitor)
for a rule-based MNQ (Micro E-mini Nasdaq-100) intraday strategy: 15m EMA
bias + 5m Fibonacci/EMA pullback and reversal entries + fib-extension/R-based
targets. See [docs/SPEC.md](docs/SPEC.md) for the full rule set and its
rationale, and [docs/GO_LIVE_CHECKLIST.md](docs/GO_LIVE_CHECKLIST.md) before
risking any real capital.

**This is not financial advice and no rule set here is verified or proven
profitable.** Every parameter is a common-practice starting point that must
be backtested and paper-traded on your own before you trust it.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes requirements.txt + pytest
```

Optional, only if you want the higher-quality Databento data source:

```bash
pip install databento
export DATABENTO_API_KEY=...
```

## Running the backtest

```bash
# Quick smoke test with free (but shallow-history) Yahoo Finance data
python -m mnq_system backtest --provider yfinance --start 2026-05-25 --end 2026-07-08

# Real verification backtest with a proper 70/30 in-sample/out-of-sample split
python -m mnq_system backtest --provider databento \
    --start 2025-07-01 --end 2026-07-08 \
    --oos-split 0.3 --output-dir ./out

# Your own CSV export (e.g. from a broker or TradingView's manual chart export)
python -m mnq_system backtest --provider csv --csv-path ./mnq_5m.csv \
    --start 2025-07-01 --end 2026-07-08
```

Run `python -m mnq_system backtest --help` for every option (risk %,
commission, slippage, account equity, disabling reversal entries, etc).

Output includes a statistics report (win rate, avg R, profit factor,
expectancy, max drawdown, Sharpe-like ratio, streaks -- see
`mnq_system/backtest/stats.py`) printed to the console, and with
`--output-dir`, `trades.csv` and `equity_curve.csv` for further analysis.

### Choosing a data source

| Provider | Cost | Intraday history | Notes |
|---|---|---|---|
| `yfinance` | Free | ~60 days (5m/15m), ~7-8 days (1m) | Good for a pipeline smoke-test only |
| `massive` | Free, no credit card | MNQ: only back to 2024-07-09 as of this writing (confirmed empirically -- their reference endpoint lists older contracts, but the aggregates endpoint has no bar data for them despite marketing "10+ years") | Best free option for a real (if ~2-year) verification backtest; rolls across dated contracts, no back-adjustment at the roll |
| `databento` | Pay-as-you-go, $125 free credit | Full CME history (MNQ launched 2019) | The option for a true 4+ year backtest; official exchange feed, card required at signup |
| `csv` | Whatever your vendor charges | Whatever you export | Also what TradingView's manual CSV export feeds into |

TradingView does not have an official API for bulk historical OHLCV export
suitable for backtesting -- use its manual chart CSV export into `--provider
csv` instead of scripting against it directly.

## Running the tests

```bash
pytest -v
```

109 tests cover the indicator math, bias/fib/candlestick/risk rule
functions, the backtest engine (including a no-lookahead determinism check),
the statistics module, and each data provider (mocked, no real network
calls).

## Project layout

```
mnq_system/
  config.py          # all tunable parameters, labeled as unverified defaults
  indicators.py       # EMA, ATR
  swings.py            # causal (no-lookahead) swing/BOS detection
  bias.py              # 15m EMA-stack + structure bias filter
  fibonacci.py         # retracement/extension zones
  candlesticks.py       # engulfing/hammer/shooting-star/doji/exhaustion-wick
  risk.py               # stop, position sizing, R:R, daily limits
  strategy.py            # pure entry/exit decision functions
  data/
    providers.py          # yfinance / Databento / CSV data sources
  backtest/
    engine.py               # event-driven backtest loop (stateful wiring)
    stats.py                 # win rate, avg R, drawdown, Sharpe-like, etc.
  cli.py                      # `backtest` (and, later, `live`) subcommands
tests/                         # pytest suite, one file per module above
docs/
  SPEC.md                       # full rule-set writeup
  GO_LIVE_CHECKLIST.md
```

## Status

- [x] Backtest engine + CLI + statistics
- [x] Unit test suite
- [ ] Signal-only live script (phase 2 -- polls a data provider, prints/logs
      long/short/exit signals, places no real orders)
