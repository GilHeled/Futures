"""
Trading-value program — does the FROZEN, validated Target-A volatility forecast
produce economic value? Study 1: dynamic volatility-adaptive stop / fixed target
adaptation (see docs/PRE_REGISTRATION_STUDY1_STOP_TARGET.md, FROZEN 2026-07-24).

The volatility model is never re-fit here; its causal out-of-sample forecast (and
the HAR naive benchmark) are consumed as cached artifacts from `market_state`.
The only variable is how the forecast is used (stop/target range). Development
only until the single pre-registered hold-out confirmation.
"""
