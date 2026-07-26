"""
Step 1a audit — offline execution-sensitivity of the frozen overnight h10 edge
(MYM/M2K/MES). No live feed, no spending: reuses the cached history + frozen
bundle. Because fill slippage is DECOUPLED from signal generation (signals gate
on the 1-tick cost hurdle; fills use fill_slippage), the entry set is invariant
to fill slippage — so we simulate the shadow book's raw trades ONCE per
instrument and re-cost net R across a slippage grid analytically.

Answers: how much per-side slippage the edge tolerates before it disappears
(breakeven), and how robust it is to adversely-missed fills. Reproducing the
edge at 3t/5t also validates faithfulness vs the recorded frozen-OOS numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dataclasses import replace

from live_validation.bundle import DEFAULT_HORIZON, build_bundle
from live_validation.inference import compute_frame
from live_validation.shadow_book import STOP_ATR_MULT, apply_slippage
from mnq_system.cli import _resolve_contract_spec
from mnq_system.config import DEFAULT_ACCOUNT_CONFIG
from mnq_system.data.providers import build_provider

SYMBOLS = ("MYM", "M2K", "MES")
TRAIN_END = pd.Timestamp("2022-12-31", tz="UTC")
OOS_START = pd.Timestamp("2023-01-01", tz="UTC")
SLIP_GRID = [0, 1, 2, 3, 4, 5, 6, 7, 8]
COMMISSION = 1.5


def _account(symbol):
    return replace(DEFAULT_ACCOUNT_CONFIG, contract=_resolve_contract_spec(symbol))


def _raw_trades(frame: pd.DataFrame) -> list:
    """Faithful single-position replay of shadow_book.on_bar, capturing RAW
    (pre-slippage) fills so R can be re-costed at any slippage."""
    pos = None
    out = []
    o, h, l, c = frame["open"].values, frame["high"].values, frame["low"].values, frame["close"].values
    ev, atr, direction = frame["ev"].values, frame["atr"].values, frame["direction"].values
    off, send = frame["off_hours"].values, frame["session_ending"].values
    idx = frame.index
    for i in range(len(frame)):
        if pos is not None:
            pos["bars"] += 1
            is_long = pos["dir"] == 1
            exit_raw = reason = None
            stop_hit = l[i] <= pos["stop"] if is_long else h[i] >= pos["stop"]
            if stop_hit:
                exit_raw = min(pos["stop"], o[i]) if is_long else max(pos["stop"], o[i])
                reason = "stop"
            elif pos["pending"]:
                exit_raw, reason = o[i], "ev_reversal"
            elif pos["bars"] >= 500:
                exit_raw, reason = c[i], "max_hold"
            elif np.isfinite(ev[i]):
                fav = ev[i] > 0 if is_long else ev[i] < 0
                if not fav:
                    pos["pending"] = True
            if exit_raw is not None:
                out.append({**pos, "exit_raw": exit_raw, "reason": reason})
                pos = None
            continue
        d = direction[i]
        if np.isfinite(d) and d != 0 and off[i] and not send[i] and np.isfinite(atr[i]) and atr[i] > 0:
            sign = int(d)
            pos = {"dir": sign, "entry_close": c[i], "stop": c[i] - STOP_ATR_MULT * atr[i] * sign,
                   "entry_ts": idx[i], "bars": 0, "pending": False}
    return out


def _net_r(tr, tick, pv, slip):
    d = "long" if tr["dir"] == 1 else "short"
    entry = apply_slippage(tr["entry_close"], d, True, tick, slip)
    exit_ = apply_slippage(tr["exit_raw"], d, False, tick, slip)
    risk = abs(entry - tr["stop"]) * pv
    if risk <= 0:
        return np.nan
    return ((exit_ - entry) * tr["dir"] * pv - COMMISSION) / risk


def _boot_p(r, seed=20260725, n=5000):
    r = np.asarray(r)
    rng = np.random.default_rng(seed)
    means = r[rng.integers(0, len(r), size=(n, len(r)))].mean(axis=1)
    return float(np.mean(means <= 0))


def run():
    provider = build_provider("databento", cache=True)
    end = pd.Timestamp("2026-07-09", tz="UTC")
    report = {}
    for sym in SYMBOLS:
        acct = _account(sym)
        tick, pv = acct.contract.tick_size, acct.contract.point_value
        full = provider.get_historical_bars(sym, pd.Timestamp("2019-05-01", tz="UTC").to_pydatetime(),
                                             end.to_pydatetime(), "5m")
        bundle = build_bundle(full.loc[:TRAIN_END], sym, tick, pv, acct, TRAIN_END, horizon=DEFAULT_HORIZON,
                              commission=COMMISSION)
        oos = full[full.index >= pd.Timestamp("2022-10-01", tz="UTC")]      # warmup + OOS
        frame = compute_frame(oos, bundle, acct).join(oos[["open", "high", "low", "close"]])
        trades = [t for t in _raw_trades(frame) if t["entry_ts"] >= OOS_START]
        report[sym] = {"tick": tick, "pv": pv, "n": len(trades),
                       "curve": [], "trades": trades}
        for slip in SLIP_GRID:
            r = np.array([_net_r(t, tick, pv, slip) for t in trades])
            r = r[np.isfinite(r)]
            report[sym]["curve"].append((slip, float(r.mean()), _boot_p(r) if slip in (3, 5) else None))
    return report


def _breakeven(curve):
    xs = [s for s, m, _ in curve]; ms = [m for s, m, _ in curve]
    for i in range(len(ms) - 1):
        if ms[i] > 0 >= ms[i + 1]:
            return xs[i] + (xs[i + 1] - xs[i]) * ms[i] / (ms[i] - ms[i + 1])
    return float("inf") if ms[-1] > 0 else (0.0 if ms[0] <= 0 else float("nan"))


def format_report(report) -> str:
    L = ["=" * 74, "OVERNIGHT EDGE — EXECUTION SENSITIVITY (frozen model, OOS 2023–2026)",
         "net avg R per trade vs per-side fill slippage (ticks); entries fixed", "=" * 74]
    for sym, d in report.items():
        L.append(f"\n{sym}  (n={d['n']} trades, tick={d['tick']}, $/pt={d['pv']})")
        L.append("  slip(t):  " + "  ".join(f"{s:>5}" for s, _, _ in d["curve"]))
        L.append("  avg R  :  " + "  ".join(f"{m:>+5.2f}" for _, m, _ in d["curve"]))
        for s, m, p in d["curve"]:
            if p is not None:
                L.append(f"    @ {s}t: avg R {m:+.3f}, bootstrap P(mean<=0)={p:.4f}")
        be = _breakeven(d["curve"])
        L.append(f"  >>> BREAKEVEN slippage ~ {be:.1f} ticks/side "
                 f"(edge disappears beyond this)")
        # adverse missed-fill tolerance at 3t: drop best-x% winners until mean<=0
        r3 = np.array([_net_r(t, d["tick"], d["pv"], 3) for t in d["trades"]])
        r3 = r3[np.isfinite(r3)]
        order = np.sort(r3)[::-1]
        adverse = None
        for f in range(0, 60):
            keep = np.sort(r3)[: len(r3) - int(len(r3) * f / 100)]  # drop top f% winners
            if keep.mean() <= 0:
                adverse = f
                break
        L.append(f"  adverse missed-fill tolerance @3t: edge survives dropping the best "
                 f"~{adverse if adverse is not None else '>60'}% of trades")
    L.append("\n" + "=" * 74)
    return "\n".join(L)


if __name__ == "__main__":
    import pathlib
    rep = run()
    out = format_report(rep)
    print(out)
    p = pathlib.Path("live_validation/results")
    p.mkdir(parents=True, exist_ok=True)
    (p / "execution_sensitivity.txt").write_text(out + "\n")
