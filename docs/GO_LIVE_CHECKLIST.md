# Before You Go Live

Do not skip steps here just because the backtest report looked good --
per `docs/SPEC.md`, none of the default parameters are verified until you've
done this.

1. **Backtest across 6-12 months of real intraday MNQ data**, covering more
   than one volatility regime (trending, choppy, high-VIX, low-VIX). Use
   `--provider databento` or a vendor CSV export -- yfinance's free intraday
   history is too shallow for this step.
2. **Use an honest in-sample/out-of-sample split** (`--oos-split 0.3` or
   similar). Only look at / tune on the in-sample portion; report the
   out-of-sample number as the real estimate.
3. **Read win rate, avg R, profit factor, and max drawdown together.** A good
   win rate with a bad avg R, or a trade count under ~30, has not been
   verified -- widen the date range rather than trusting a thin sample.
4. **Paper/forward-test** the exact fixed rule set for 4-8 weeks on the
   (forthcoming) signal-only live script before committing real capital.
5. **Confirm current MNQ contract specs** (tick size, point value, margin)
   directly with your broker or CME -- the values in `mnq_system/config.py`
   are illustrative and can go stale.
6. **Start live with reduced size** relative to backtest sizing, and only
   scale up once live results track the backtest reasonably well.
7. **Define (and don't override under pressure)** your max daily loss and
   max trades per day -- defaults are in `RiskConfig`
   (`mnq_system/config.py`), 3% daily loss cap and 5 trades/day.
8. No rule set guarantees future performance. Markets change regime; review
   the system periodically rather than trusting a single validation pass
   forever.
