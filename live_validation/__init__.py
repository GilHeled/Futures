"""
Live execution-validation harness for the overnight h10 edge (MYM/M2K/MES).

This is VALIDATION, not production: it runs the frozen model + frozen
decision layer forward against live Databento data in shadow mode (no
broker, no orders) and logs, for every signal, the expected fill (our
backtest assumption) vs. the bid/ask-implied fill (crossing the real
spread), the realized slippage, and the realized P&L. The single question
it answers is the one backtesting cannot: does real overnight execution
behave consistently with the 3-5 tick slippage assumption the edge
survived?

Everything upstream (predictive model, EV estimation, dynamic_ev exit,
trading-hours gate, sizing) is FROZEN. See docs and the plan for the
pre-registered Go/No-Go criteria.
"""
