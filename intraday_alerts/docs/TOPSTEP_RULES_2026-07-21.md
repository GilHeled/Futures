# Topstep rule snapshot — archived for audit

**Retrieved:** 2026-07-21 from the official Topstep Help Center. Frozen for this
research so the exact rules used remain auditable if Topstep later changes them.
The machine-readable copy lives in `intraday_alerts/config.py` (TOPSTEP_* constants).

**Sources (official):**
- Trading Combine Parameters — https://help.topstep.com/en/articles/8284197-trading-combine-parameters
- Maximum Loss Limit — https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit
- Daily Loss Limit (Combine & Express Funded) — https://help.topstep.com/en/articles/10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account

## $50,000 account — verified values

- **Maximum Loss Limit (MLL): $2,000.** Starts $2,000 below the $50,000 balance
  (at $48,000). Trails the **end-of-day** balance upward, never moves down, and
  **locks permanently once it reaches the $50,000 starting balance.** Monitored
  **intraday in real time** (realized + unrealized P&L); if net P&L hits it at any
  point the account is liquidated immediately.
- **Daily Loss Limit (DLL): $1,000** (fixed at purchase for $50k). When net P&L
  hits it intraday, open positions are flattened, pending orders canceled, no new
  trades until the next session. It is a **forced break, not a rule violation.**
  (A manual trailing Personal DLL can alternatively be set.)
- **Contract limit:** **5 minis / 50 micros** (10:1 ratio), subject to the Scaling
  Plan (starts low, increases with profit). **This research uses 1 micro.**
- **Session:** 5:00 PM CT → 3:10 PM CT. This research conservatively enters only
  10:00–15:00 ET and force-flats 15:55 ET — well inside the 3:10 PM CT (~16:10 ET)
  cutoff, so no overnight and a safety margin to the close.

## Stage distinctions

- **Trading Combine** and **Express Funded (XFA)** share these DLL parameters (DLL
  optional at purchase; identical when set). This research targets the
  **Combine → Express Funded** rule set.
- **Live Funded (LFA):** the DLL is applied automatically. Re-verify LFA-specific
  mechanics before any live-funded deployment.

## Applied buffer (research choice, not a Topstep rule)

- **20% internal safety buffer** → effective daily stop **$800**, effective trailing
  MLL **$1,600**. The simulator enforces these *effective* limits prospectively.
