"""
Stop/target overlay simulator (§4, §7). The volatility forecast controls ONLY the
range distance D(t):
  - fixed take-profit  : TP = entry ± m·D(t0)      (set at entry, never updated)
  - dynamic ratcheting stop : distance k·D(t) from the running favorable extreme,
                              updated each completed bar, may only TIGHTEN.

MATCHED ENTRIES: the entry calendar is computed from the strategy signals and a
baseline-only occupancy (session-flat + baseline exit, NO stop/target), so it is
identical for every volatility source. Each arm then executes the SAME entries and
differs only in its stop/target exits (§5 "entries identical across arms").

Execution (frozen §7): levels set at the close of bar t are active during bar t+1
(entry bar is fill-only); entry & baseline exits fill at the next bar's open ±
slippage; same-bar stop&target ⇒ STOP-FIRST; gap ⇒ stop fills at the bar open,
target at the level; session-flat 15:55 ET overrides.
"""
from __future__ import annotations

from datetime import time as dt_time

import numpy as np
import pandas as pd

from trading_value import config as C

_SLIP = C.SLIP_TICKS_PER_SIDE * C.TICK
_FIXED_COST = C.COMMISSION_RT + C.SPREAD_TICKS * C.TICK * C.POINT_VALUE   # $/trade (commission+spread)
_FLAT = dt_time(*C.FLAT_BY)


def _arrays(bars, signals):
    return {
        "o": bars["open"].values.astype(float), "h": bars["high"].values.astype(float),
        "lo": bars["low"].values.astype(float), "c": bars["close"].values.astype(float),
        "date": bars["et_date"].values,
        "is_flat": np.array([t >= _FLAT for t in bars.index.tz_convert(C.TIMEZONE).time]),
        "edir": signals["entry_dir"].values.astype(int),
        "xl": signals["exit_long"].values.astype(bool),
        "xs": signals["exit_short"].values.astype(bool),
    }


def entry_calendar(bars, signals, entry_ok) -> list:
    """Vol-source-INDEPENDENT list of (signal_bar_index, direction). Occupancy is
    determined by baseline exits + session-flat only, so every arm shares it."""
    a = _arrays(bars, signals)
    ok = np.asarray(entry_ok, dtype=bool)
    n = len(bars)
    cal = []
    in_pos = False; pos_dir = 0; entry_bar = -1
    pend_entry = 0; pend_exit = False; pend_src = -1
    for i in range(n):
        if (pend_entry or pend_exit) and pend_src >= 0 and a["date"][i] != a["date"][pend_src]:
            pend_entry, pend_exit = 0, False
        if pend_exit and in_pos:
            in_pos, pend_exit = False, False
        if pend_entry != 0 and not in_pos:
            in_pos, pos_dir, entry_bar, pend_entry = True, pend_entry, i, 0
        if in_pos and entry_bar != i:
            if a["is_flat"][i]:
                in_pos = False
            elif (pos_dir == 1 and a["xl"][i]) or (pos_dir == -1 and a["xs"][i]):
                pend_exit, pend_src = True, i
        if not in_pos and pend_entry == 0 and not pend_exit and a["edir"][i] != 0 and ok[i]:
            pend_entry, pend_src = int(a["edir"][i]), i
            cal.append((i, int(a["edir"][i])))
    return cal


def simulate(bars: pd.DataFrame, signals: pd.DataFrame, d_series, entry_ok,
             k: float, m: float) -> pd.DataFrame:
    a = _arrays(bars, signals)
    Dv = np.asarray(d_series, dtype=float)
    n = len(bars)
    o, h, lo, c, date, is_flat = a["o"], a["h"], a["lo"], a["c"], a["date"], a["is_flat"]
    idx = bars.index                                   # bar timestamps (for post-mortem)
    cal = entry_calendar(bars, signals, entry_ok)
    trades = []

    for sig_i, d in cal:
        ei = sig_i + 1
        if ei >= n or date[ei] != date[sig_i] or not np.isfinite(Dv[ei]):
            continue
        entry_fill = o[ei] + d * _SLIP
        De = Dv[ei]
        tp = entry_fill + d * m * De          # FIXED at entry
        stop = entry_fill - d * k * De
        extreme = entry_fill
        # ratchet on entry-bar close (active next bar); entry bar is fill-only
        if d == 1:
            extreme = max(extreme, h[ei]); stop = max(stop, extreme - k * Dv[ei])
        else:
            extreme = min(extreme, lo[ei]); stop = min(stop, extreme + k * Dv[ei])

        pend_base = False
        exited = False
        j = ei + 1
        while j < n and date[j] == date[ei]:
            if pend_base:
                fill = o[j] - d * _SLIP
                trades.append((date[ei], idx[ei], d, entry_fill, fill, "baseline")); exited = True; break
            if is_flat[j]:
                fill = c[j] - d * _SLIP
                trades.append((date[ei], idx[ei], d, entry_fill, fill, "session_flat")); exited = True; break
            if d == 1:
                stop_gap, tp_gap = o[j] <= stop, o[j] >= tp
                stop_hit, tp_hit = stop_gap or lo[j] <= stop, tp_gap or h[j] >= tp
            else:
                stop_gap, tp_gap = o[j] >= stop, o[j] <= tp
                stop_hit, tp_hit = stop_gap or h[j] >= stop, tp_gap or lo[j] <= tp
            if stop_hit:                                   # stop-first if both
                fill = o[j] if stop_gap else (stop - d * _SLIP)
                trades.append((date[ei], idx[ei], d, entry_fill, fill, "stop")); exited = True; break
            if tp_hit:
                fill = tp if tp_gap else (tp - d * _SLIP)
                trades.append((date[ei], idx[ei], d, entry_fill, fill, "target")); exited = True; break
            # ratchet (close of j) + queue baseline exit for next open
            if d == 1:
                extreme = max(extreme, h[j]); stop = max(stop, extreme - k * Dv[j])
                if a["xl"][j]:
                    pend_base = True
            else:
                extreme = min(extreme, lo[j]); stop = min(stop, extreme + k * Dv[j])
                if a["xs"][j]:
                    pend_base = True
            j += 1
        if not exited:                                     # safety (session-flat should catch)
            last = min(j, n - 1)
            trades.append((date[ei], idx[ei], d, entry_fill, c[last] - d * _SLIP, "eod"))

    df = pd.DataFrame(trades, columns=["entry_date", "entry_ts", "dir", "entry_fill", "exit_fill", "reason"])
    if len(df):
        df["pnl_usd"] = (C.SIZE * C.POINT_VALUE * df["dir"] * (df["exit_fill"] - df["entry_fill"])
                         - C.SIZE * _FIXED_COST)
    else:
        df["pnl_usd"] = pd.Series(dtype=float)
    return df


def daily_pnl(trades: pd.DataFrame, all_dates) -> pd.Series:
    s = trades.groupby("entry_date")["pnl_usd"].sum() if len(trades) else pd.Series(dtype=float)
    return s.reindex(pd.Index(sorted(set(all_dates))), fill_value=0.0)
