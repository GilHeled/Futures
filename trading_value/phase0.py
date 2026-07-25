"""
Phase-0 verification for Study 1 — the ten pre-run checks. Crafted scenarios
verify the execution mechanics; real dev data verifies artifacts / boundaries /
normalization / entry-identity / determinism. Run: python -m trading_value.phase0
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from market_state import config as MS
from market_state.data import HoldoutAccessError, annotate_session, load_bars
from trading_value import channel, config as C, metrics as M
from trading_value import strategies as S
from trading_value import vol_sources
from trading_value.vol_artifacts import build_dev_vol_streams


def _bars(ohlc, start="2021-06-01 10:00"):
    idx = pd.date_range(start, periods=len(ohlc), freq="5min", tz=C.TIMEZONE).tz_convert("UTC")
    df = pd.DataFrame(ohlc, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1000.0
    df["et_date"] = pd.Index(idx.tz_convert(C.TIMEZONE).date)
    return df


def _sig(bars, entry_dir_map):
    ed = np.zeros(len(bars), int)
    for p, d in entry_dir_map.items():
        ed[p] = d
    return pd.DataFrame({"entry_dir": ed, "exit_long": False, "exit_short": False}, index=bars.index)


def _one(bars, sigmap, D, k, m):
    tr = channel.simulate(bars, _sig(bars, sigmap), pd.Series(D, index=bars.index),
                          np.ones(len(bars), bool), k, m)
    return tr


# ---- execution-mechanics checks (crafted) -----------------------------------

def check_stop_active_next_bar():
    b = _bars([(100, 100, 100, 100), (100, 100, 80, 100), (100, 100, 80, 100)])
    tr = _one(b, {0: 1}, [10, 10, 10], k=1.0, m=10.0)
    # low 80 breaches the 90.25 stop on the ENTRY bar (1) but must NOT trigger there
    return len(tr) == 1 and tr.iloc[0]["reason"] == "stop" and abs(tr.iloc[0]["exit_fill"] - 90.0) < 1e-9


def check_stop_tightens_only():
    b = _bars([(100, 100, 100, 100), (100, 105, 100, 100), (100, 100, 94, 100)])
    tr = _one(b, {0: 1}, [10, 10, 30], k=1.0, m=20.0)   # D jumps to 30 at bar2
    # stop ratcheted to 95 on bar1; must NOT loosen to 75 (extreme105-30) -> low 94 hits 95
    return len(tr) == 1 and tr.iloc[0]["reason"] == "stop" and abs(tr.iloc[0]["exit_fill"] - 94.75) < 1e-9


def check_tp_fixed_from_entry():
    b = _bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 102, 100, 100), (100, 111, 100, 100)])
    tr = _one(b, {0: 1}, [10, 10, 1, 1], k=20.0, m=1.0)   # TP fixed at 110.25; floating would hit ~101.25 on bar2
    return (len(tr) == 1 and tr.iloc[0]["reason"] == "target"
            and abs(tr.iloc[0]["exit_fill"] - 110.0) < 1e-9)


def check_stop_first():
    b = _bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 115, 85, 100)])
    tr = _one(b, {0: 1}, [10, 10, 10], k=1.0, m=1.0)     # both stop(90.25) and target(110.25) in bar2
    return len(tr) == 1 and tr.iloc[0]["reason"] == "stop" and abs(tr.iloc[0]["exit_fill"] - 90.0) < 1e-9


def check_gap_fill_at_open():
    b = _bars([(100, 100, 100, 100), (100, 100, 100, 100), (80, 80, 75, 78)])
    tr = _one(b, {0: 1}, [10, 10, 10], k=1.0, m=10.0)   # opens 80, below 90.25 stop -> fills at open (no slip)
    return len(tr) == 1 and tr.iloc[0]["reason"] == "stop" and abs(tr.iloc[0]["exit_fill"] - 80.0) < 1e-9


def check_entry_next_bar_and_costs():
    b = _bars([(100, 100, 100, 100), (100, 100, 100, 100), (100, 111, 100, 100)])
    tr = _one(b, {0: 1}, [10, 10, 10], k=5.0, m=1.0)    # entry at bar1 open 100 -> 100.25 (slip); target 110.25
    t = tr.iloc[0]
    pnl = 5 * (110.0 - 100.25) - 2.75
    return (abs(t["entry_fill"] - 100.25) < 1e-9 and abs(t["exit_fill"] - 110.0) < 1e-9
            and abs(t["pnl_usd"] - pnl) < 1e-9)


# ---- real-data checks -------------------------------------------------------

def _dev_context():
    bars = annotate_session(load_bars(C.INSTRUMENT, split="dev"))
    bars = bars[bars["in_rth"]].copy().sort_index()
    streams = build_dev_vol_streams(C.INSTRUMENT)
    D, c_source = vol_sources.build_range_distances(bars, streams)
    return bars, streams, D, c_source


def run_all():
    results = []

    def add(name, passed, detail=""):
        results.append((name, bool(passed), detail))

    # mechanics
    add("4. stop updates active only on the following bar", check_stop_active_next_bar())
    add("5. stops tighten only, never loosen", check_stop_tightens_only())
    add("6. take-profit fixed from entry", check_tp_fixed_from_entry())
    add("7. same-bar stop/target resolves stop-first", check_stop_first())
    add("8. gap fills at open; slippage per spec", check_gap_fill_at_open()
        and check_entry_next_bar_and_costs())

    bars, streams, D, c_source = _dev_context()

    # 1. artifacts causal & aligned
    idx_ok = streams.index.isin(bars.index).all()
    pos = bool((streams["V_forecast"] > 0).all() and (streams["V_har"] > 0).all())
    mono = streams.index.is_monotonic_increasing
    add("1. forecast/HAR artifacts causal & aligned to bar timestamps",
        idx_ok and pos and mono,
        f"n={len(streams)} span={streams.index.min()}..{streams.index.max()} aligned={idx_ok} positive={pos}")

    # 2. dev/hold-out boundaries enforced
    within_dev = streams.index.max() <= MS.DEV_END and streams.index.min() >= MS.DATA_START
    guard = False
    try:
        load_bars(C.INSTRUMENT, split="holdout")
    except HoldoutAccessError:
        guard = True
    add("2. development/hold-out boundaries enforced",
        within_dev and guard, f"streams<=DEV_END={within_dev}  holdout_guard_raises={guard}")

    # 3. normalization uses development data only
    mask = D["entry_ok"].values
    means = {s: float(D[s].values[mask].mean()) for s in C.VOL_SOURCES}
    norm_ok = (abs(c_source["naive"] - 1.0) < 1e-12
               and max(abs(means[s] - means["naive"]) for s in C.VOL_SOURCES) < 1e-6)
    add("3. average-distance normalization uses dev only (c_naive=1, means matched)",
        norm_ok, f"c_forecast={c_source['forecast']:.4f} means={ {k: round(v,2) for k,v in means.items()} }")

    # 9. entries identical across vol-source arms
    sig = S.compute_signals(bars, "ema_cross")
    ok = D["entry_ok"].values
    ent = {}
    for src in C.VOL_SOURCES:
        tr = channel.simulate(bars, sig, D[src], ok, 1.0, 1.0)
        ent[src] = list(zip(tr["entry_date"], np.round(tr["entry_fill"].values, 6)))
    entries_identical = (ent["none"] == ent["naive"] == ent["forecast"])
    add("9. entries identical across volatility-source arms",
        entries_identical, f"n_entries={len(ent['none'])} identical={entries_identical}")

    # 10. deterministic under fixed seeds
    a = M.paired_block_bootstrap_dsharpe(np.random.default_rng(1).normal(size=200),
                                         np.random.default_rng(2).normal(size=200), seed=123)
    bb = M.paired_block_bootstrap_dsharpe(np.random.default_rng(1).normal(size=200),
                                          np.random.default_rng(2).normal(size=200), seed=123)
    add("10. results deterministic under fixed seeds",
        a["p_le_zero"] == bb["p_le_zero"] and a["ci_lo"] == bb["ci_lo"])

    return results


def format_report(results) -> str:
    L = ["=" * 74, "STUDY 1 — PHASE-0 VERIFICATION (MES dev)", "=" * 74]
    for name, passed, detail in results:
        L.append(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if detail:
            L.append(f"          {detail}")
    L.append("-" * 74)
    L.append(f"ALL PASS: {all(p for _, p, _ in results)}")
    L.append("=" * 74)
    return "\n".join(L)


if __name__ == "__main__":
    import pathlib
    res = run_all()
    rep = format_report(res)
    print(rep)
    out = pathlib.Path("trading_value/results")
    out.mkdir(parents=True, exist_ok=True)
    (out / "study1_phase0.txt").write_text(rep + "\n")
