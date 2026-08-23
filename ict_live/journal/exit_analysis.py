"""Exit-analysis measurement layer — diagnostics ONLY, no optimization, engine frozen.

Purpose: separate three questions the baseline expectancy result raised —
  1. ENTRY quality  — does price move in the intended direction after entry? (MFE distribution)
  2. EXIT model     — is the fixed distant-liquidity target creating the fat tail? (target-hit ratio,
                      MFE reached vs target distance, R-levels reached before stop)
  3. MANAGEMENT     — could partial/BE/trailing/time exits convert the distribution? (how often a
                      STOPPED trade had already run to +1R/+2R/+3R MFE)

Per accepted TRADE it records: MAE, MFE, time-to-MFE, time-to-stop, whether 1R/2R/3R was reached
before stop, distance to target (reward_R), target-hit-before-stop, and the outcome. Aggregation
answers the three questions descriptively. Reuses engine.outcomes (unchanged); time-to-MFE and
time-to-stop are computed here from the bars so outcomes.py is not modified.

Run under the venv: .venv/bin/python -m ict_live.journal.exit_analysis
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import execution_quality as EQ
from ict_live.engine import outcomes as OUT
from ict_live.engine import pipeline
from ict_live.market.calendar import Calendar
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod

OUT_PATH = "ict_live/research/datasets/exit_records.jsonl"
RESULT_MD = "ict_live/research/RESULT_exit_analysis.md"
_TF_MIN_STOP = 2.0


def diagnostics(outcome: dict, bars, *, direction: str, entry: float, stop: float,
                target, risk: float) -> dict:
    """Per-trade exit diagnostics from an outcomes payload + the bar series."""
    base = {"triggered": False, "outcome": outcome.get("course_execution", {}).get("result")}
    if outcome.get("status") != "labelled" or outcome.get("fill_index") is None:
        return base
    short = direction == "short"
    fill = outcome["fill_index"]
    course = outcome["course_execution"]
    final = course["final_index"]
    fixed, liq, exc = outcome["fixed_r"], outcome["liquidity_target"], outcome["excursion"]

    def stop_hit(b):
        return b.high >= stop if short else b.low <= stop
    stop_bar = next((j for j in range(fill, final + 1) if stop_hit(bars[j])), None)
    # time to the maximum favorable excursion (over the realized life fill..final)
    best, best_j = None, fill
    for j in range(fill, final + 1):
        fav = (entry - bars[j].low) if short else (bars[j].high - entry)
        if best is None or fav > best:
            best, best_j = fav, j
    return {
        "triggered": True,
        "outcome": course["result"],
        "result_R": course["realized_R"],
        "reward_R": round(abs(entry - target) / risk, 3) if target is not None else None,
        "mfe_R": exc["mfe_R"], "mae_R": exc["mae_R"],
        "bars_to_mfe": best_j - fill,
        "bars_to_stop": (stop_bar - fill) if stop_bar is not None else None,
        "bars_to_final": course["bars_to_final"],
        "bars_to_target": liq["bars_to_target"],
        "r1_before_stop": fixed["r1_hit"], "r2_before_stop": fixed["r2_hit"],
        "r3_before_stop": fixed["r3_hit"],
        "target_before_stop": liq["reached_before_stop"],
    }


def generate(symbols=("MES", "MNQ"), *, signal_tf="1H", entry_tf="15m", dev_start="2019-05-01",
             dev_end="2025-01-01", stride=6, window=240,
             horizon_bars=OUT.DEFAULT_HORIZON_BARS, out_path=OUT_PATH) -> dict:
    assert dev_end <= "2025-01-01", "dev_end must not enter the locked OOS"
    cal = Calendar()
    rows = []
    for symbol in symbols:
        bars5 = data_mod.load_5m(symbol, start=dev_start, end=dev_end)
        segments = rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))
        seen = set()
        for si, seg in enumerate(segments):
            if si == 0:
                continue
            sig = data_mod.resample(seg.bars, signal_tf)
            ref_all = data_mod.resample(seg.bars, entry_tf)
            for k in range(window, len(sig), stride):
                cc = sig[k].close_time
                ms = pipeline.analyze(sig[max(0, k - window + 1):k + 1], signal_tf,
                                      refine_bars=[b for b in ref_all if b.close_time <= cc],
                                      min_stop=_TF_MIN_STOP)
                win = ms.recommendation.setup
                if win is None:
                    continue
                key = (seg.contract, win.id)
                if key in seen:
                    continue
                seen.add(key)
                outcome = OUT.label_setup(id=win.id, symbol=symbol, tf=signal_tf,
                                          direction=win.direction, entry=win.entry, stop=win.stop,
                                          target=win.target, decision_index=k, bars=sig,
                                          horizon_bars=horizon_bars, calendar=cal)
                diag = diagnostics(outcome, sig, direction=win.direction, entry=win.entry,
                                   stop=win.stop, target=win.target, risk=abs(win.entry - win.stop))
                rows.append({"scene_id": f"{symbol}:{seg.contract}:{signal_tf}:{sig[k].open_time.isoformat()}",
                             "symbol": symbol, "direction": "LONG" if win.direction == "long" else "SHORT",
                             "execution": EQ.assess(ms).execution, **diag})
        print(f"  {symbol}: {sum(1 for r in rows if r['symbol'] == symbol)} trades")
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("\n".join(json.dumps(r, default=str) for r in rows))
    return {"symbols": list(symbols), "trades": len(rows), "out_path": out_path}


def _pct(num, den):
    return round(num / den, 3) if den else None


def _quantiles(xs):
    xs = sorted(xs)
    if not xs:
        return {}
    def q(p):
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2)
    return {"min": round(xs[0], 2), "p25": q(.25), "median": q(.5), "p75": q(.75),
            "p90": q(.9), "max": round(xs[-1], 2)}


def analyze(rows: list[dict], *, execution="TRADE") -> dict:
    trades = [r for r in rows if r.get("triggered") and (execution is None or r.get("execution") == execution)]
    n = len(trades)
    if not n:
        return {"execution": execution, "n": 0}
    stopped = [r for r in trades if r["outcome"] == "STOP"]
    mfes = [r["mfe_R"] for r in trades if r["mfe_R"] is not None]

    def reached_mfe(thr, subset):
        return _pct(sum(1 for r in subset if (r["mfe_R"] or 0) >= thr), len(subset))
    mfe_buckets = {}
    for lo, hi in [(0, .5), (.5, 1), (1, 2), (2, 3), (3, 99)]:
        mfe_buckets[f"[{lo},{hi})"] = sum(1 for r in trades if lo <= (r["mfe_R"] or 0) < hi)
    return {
        "execution": execution, "n": n, "n_stopped": len(stopped),
        "target_hit_ratio": _pct(sum(1 for r in trades if r["target_before_stop"] is True), n),
        "reached_1R_before_stop": _pct(sum(1 for r in trades if r["r1_before_stop"] is True), n),
        "reached_2R_before_stop": _pct(sum(1 for r in trades if r["r2_before_stop"] is True), n),
        "reached_3R_before_stop": _pct(sum(1 for r in trades if r["r3_before_stop"] is True), n),
        "reward_R_distn": _quantiles([r["reward_R"] for r in trades if r["reward_R"] is not None]),
        "mfe_R_distn": _quantiles(mfes),
        "mfe_buckets": mfe_buckets,
        # ENTRY quality: did price move our way at all?
        "entry_mfe>=0.5R": reached_mfe(0.5, trades),
        "entry_mfe>=1R": reached_mfe(1.0, trades),
        # MANAGEMENT signal: STOPPED trades that had already run in our favor
        "stopped_that_reached_1R_mfe": reached_mfe(1.0, stopped),
        "stopped_that_reached_2R_mfe": reached_mfe(2.0, stopped),
        "bars_to_mfe_distn": _quantiles([r["bars_to_mfe"] for r in trades if r["bars_to_mfe"] is not None]),
        "bars_to_stop_distn": _quantiles([r["bars_to_stop"] for r in stopped if r["bars_to_stop"] is not None]),
        "bars_to_target_distn": _quantiles([r["bars_to_target"] for r in trades if r["bars_to_target"] is not None]),
    }


def render_md(byexec: dict) -> str:
    L = ["# Exit analysis — accepted TRADEs (dev, engine frozen; diagnostics only)", ""]
    for ex, a in byexec.items():
        if not a.get("n"):
            continue
        L += [f"## {ex}  (n={a['n']}, stopped={a['n_stopped']})", "",
              f"- target hit ratio (before stop): **{a['target_hit_ratio']}**",
              f"- reached before stop: 1R {a['reached_1R_before_stop']} · 2R {a['reached_2R_before_stop']} · 3R {a['reached_3R_before_stop']}",
              f"- reward_R (target distance) distn: {a['reward_R_distn']}",
              f"- MFE_R distn: {a['mfe_R_distn']}",
              f"- MFE buckets: {a['mfe_buckets']}",
              f"- ENTRY — moved >=0.5R: {a['entry_mfe>=0.5R']} · moved >=1R: {a['entry_mfe>=1R']}",
              f"- MANAGEMENT — of STOPPED trades, reached +1R MFE first: {a['stopped_that_reached_1R_mfe']} · +2R: {a['stopped_that_reached_2R_mfe']}",
              f"- timing (bars): to-MFE {a['bars_to_mfe_distn']} · to-stop {a['bars_to_stop_distn']} · to-target {a['bars_to_target_distn']}",
              ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    rep = generate()
    rows = [json.loads(l) for l in Path(OUT_PATH).read_text().splitlines() if l.strip()]
    byexec = {ex: analyze(rows, execution=ex) for ex in ("TRADE", "PASS")}
    Path(RESULT_MD).write_text(render_md(byexec))
    print(json.dumps({**rep, "TRADE": byexec["TRADE"]}, indent=1, default=str))
