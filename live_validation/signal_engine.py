"""
Streaming signal engine. On each new 5m bar it recomputes `compute_frame`
over a capped rolling buffer and returns that latest bar's signal. Because
`compute_frame` is causal (row t depends only on rows <= t), taking the
last row of the buffer each step reproduces the batch computation at that
position -- which the parity test asserts directly. A capped buffer keeps
live memory/CPU bounded; it must exceed the model's feature lookback +
rising_edge debounce (verified by the truncation-stability check).
"""
from __future__ import annotations

import pandas as pd

from live_validation.inference import compute_frame

# Generous default: long enough that truncating to it does not change the
# tail feature/EV/debounce values vs. full history (checked in the parity test).
DEFAULT_BUFFER_BARS = 3000


def batch_frame(bars, bundle, account):
    """Reference batch computation over the whole frame (parity target)."""
    return compute_frame(bars, bundle, account)


class StreamingSignalEngine:
    def __init__(self, bundle, account, buffer_bars: int = DEFAULT_BUFFER_BARS):
        self.bundle = bundle
        self.account = account
        self.buffer_bars = buffer_bars
        self._buf = None  # pd.DataFrame of recent bars

    def push(self, ts: pd.Timestamp, ohlcv: dict) -> dict:
        """Append one bar, recompute on the rolling buffer, return the
        latest bar's signal row as a dict (ev, atr, cost_hurdle, direction,
        entry_allowed, session_ending, off_hours)."""
        row = pd.DataFrame([ohlcv], index=pd.DatetimeIndex([ts]))
        self._buf = row if self._buf is None else pd.concat([self._buf, row])
        if len(self._buf) > self.buffer_bars:
            self._buf = self._buf.iloc[-self.buffer_bars:]
        frame = compute_frame(self._buf, self.bundle, self.account)
        last = frame.iloc[-1]
        return {
            "ts": ts,
            "ev": float(last["ev"]) if pd.notna(last["ev"]) else None,
            "atr": float(last["atr"]) if pd.notna(last["atr"]) else None,
            "cost_hurdle": float(last["cost_hurdle"]) if pd.notna(last["cost_hurdle"]) else None,
            "direction": (int(last["direction"]) if pd.notna(last["direction"]) else 0),
            "entry_allowed": bool(last["entry_allowed"]),
            "session_ending": bool(last["session_ending"]),
            "off_hours": bool(last["off_hours"]),
        }
