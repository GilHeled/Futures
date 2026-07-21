# MNQ Rule-Based Day-Trading System -- Specification

This is the rule set the backtest and (future) live scripts execute. It follows
a conventional multi-timeframe EMA-bias + Fibonacci-entry + candlestick-confirmation
design used widely in retail/prop discretionary and systematic trading. **Every
numeric default below is a starting hypothesis borrowed from common practice --
none of it is verified for MNQ specifically until you run it through the
verification workflow in the last section.** Past performance of any rule set,
backtested or not, does not guarantee future results.

## Instrument

Micro E-mini Nasdaq-100 (MNQ), CME Globex. Point value $2/point, tick size 0.25
($0.50/tick) as of this writing -- **confirm current contract specs with your
broker or CME before using them for real position sizing**, they are not fixed
forever.

## Timeframes

| Role | Timeframe | Purpose |
|---|---|---|
| Bias | 15-minute | Is the system allowed to look for longs, shorts, or neither |
| Entry | 5-minute | Where fib zones, EMA pullbacks, and candlestick confirmation are evaluated |

## Bias

Computed on the 15-minute chart with a 9/20/50 EMA stack:

- **Bullish**: price > EMA50, EMA9 > EMA20 > EMA50, EMA50 sloping up over the
  last 10 bars, AND swing structure shows higher-highs/higher-lows.
- **Bearish**: mirror image.
- **Neutral**: anything else (tangled EMAs, structure disagrees with the EMA
  stack, or not enough history yet) -- the system stands aside.

Bias never generates an entry by itself; it only gates direction.

## Entry -- Pullback (continuation, trades with the bias)

1. Identify the most recent impulse leg in the bias direction (swing low ->
   swing high for bullish, mirrored for bearish), using swing pivots
   confirmed only from bars strictly in the past (no lookahead).
2. Price must retrace into the **golden zone (50%-61.8%)** or, in a strong
   trend, the shallower **38.2% zone**.
3. The retracement zone must have **EMA20/50 confluence** (within 1x ATR(14)).
4. A confirmation candle must fire in the bias direction: bullish/bearish
   engulfing, or hammer/shooting star.
5. Retracements beyond 78.6% invalidate the setup.

## Entry -- Reversal (change of character, higher R:R / lower win rate)

1. Requires a **break of structure**: price takes out the most recent
   confirmed swing high/low.
2. Followed by a **failed retest** of that broken level (price returns near
   it but fails to reclaim it).
3. Confirmed by a reversal candle (engulfing or hammer/shooting star) at the
   retest.
4. Sized to a stricter minimum reward:risk (2.0R default, vs. 1.5R for
   pullbacks) given the lower win-rate profile of counter-trend setups.

## Stop, Targets, Position Sizing

- **Stop**: structure-based -- just beyond the swing low/high (pullback) or
  the broken level (reversal), plus a small tick buffer. Never widened once
  set.
- **Targets**: fib extensions of the impulse leg -- 127.2% (partial exit,
  move stop to breakeven) and 161.8% (full exit / trail). Reversal trades use
  R-multiples of the stop distance instead, since they don't have a clean
  prior impulse leg to extend.
- **Position size**: `floor(account_equity * risk_pct / (stop_distance_pts * point_value))`,
  0.5% risk per trade by default, rounds down to whole contracts (skips the
  trade rather than rounding up).
- **Daily limits**: stop trading for the day after -3% account equity, 3
  consecutive losses, or 5 trades, whichever comes first.
- **Session**: only looks for new entries in the 9:30-11:30 ET and 15:00-16:00
  ET windows (half size in the 12:00-13:30 ET chop window), and force-flattens
  any open position before the session close.

## Known simplifications (read before trusting results)

- Swing pivots require `lookback` bars of confirmation on both sides before
  they're used for anything, by design -- this guarantees no lookahead, but
  means a fresh pullback low/high isn't available as a stop reference until
  it's confirmed; the stop may reference an earlier, wider swing level in the
  meantime rather than the exact bar that triggered the entry.
- Reversal-entry targets use R-multiples of the stop distance rather than fib
  extensions (pullback targets do use fib extensions).
- Backtest fills assume the trigger price itself (stop level, target level,
  or bar close for flattens) plus configurable slippage ticks and a flat
  commission per contract -- no partial fills, no order-book depth modeling.

## Verification workflow (do this before trusting any of the above)

1. **Data**: get 6-12 months of MNQ intraday history across multiple
   volatility regimes. yfinance's free data is too shallow for this (~60 days
   of intraday retention) -- use `--provider databento` or another vendor's
   export via `--provider csv`.
2. **Split**: use `--oos-split 0.3` (or similar) to hold out the most recent
   30% of the range untouched while you look at the first 70%.
3. **Tune** EMA periods, fib tolerance, stop buffer, etc. only on the
   in-sample portion, if at all.
4. **Report the out-of-sample number honestly** -- that's the estimate that
   matters, not the in-sample one. Always read win rate, avg R, profit
   factor, and max drawdown together; a good win rate with a bad avg R (or a
   trade count under ~30) is not a working system.
5. **Paper trade** the fixed rule set for 4-8 weeks before any real capital,
   using the (forthcoming) signal-only live script.
6. Confirm current MNQ contract specs (tick size, point value, margin) with
   your broker before sizing real positions.

No rule set here is a guarantee of future performance.
