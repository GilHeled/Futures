"""
Market-State Prediction — Target A: Expected Realized Volatility (MES).

A NEW research program (after the closed intraday-direction null): predict the
predictable axis of the market — forward realized VOLATILITY (a second moment),
not direction. The scientific contract is frozen in
docs/PRE_REGISTRATION_TARGET_A.md (mirrored numerically in config.py).

This package holds only model-INDEPENDENT primitives (data boundary, RV/LMP
labels, evaluation metrics, candidate baselines, purged walk-forward, block
bootstrap). The prediction model and concrete feature list are defined in
docs/IMPLEMENTATION.md, approved before development, and only then locked (§3).
No modeling run happens until that implementation document is approved and the
Phase-0 unit tests pass.
"""
