"""
Intraday futures ML alert system (Topstep-compatible) — a NEW project,
independent of the overnight strategy. Reuses the prior project's
engineering discipline (cached data, causal validation, provider-adapter
pattern) but NONE of its model or trading assumptions.

Phase 1 is pure research answering one binary question: does a
statistically robust, net-of-cost INTRADAY edge exist? All research design
is frozen (see intraday_alerts/config.py and docs/PRE_REGISTRATION.md).
No modeling runs until Phase 0 verification (unit tests) passes.
"""
