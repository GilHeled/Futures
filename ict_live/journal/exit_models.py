"""Exit-model characterization — simulate a FIXED set of exit philosophies on the frozen engine's
trades (see PRE_REG_exit_models.md). Diagnostics only: no parameter search, no optimization. Engine
untouched — only the exit rule varies; entries/stops/structural targets come from the engine.

Each model returns a single realized R per trade under a deterministic, no-look-ahead rule. Intrabar
ambiguity (favorable level and governing stop both touched in one bar) → AMBIGUOUS (excluded from R).

Run under the venv: .venv/bin/python -m ict_live.journal.exit_models
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import execution_quality as EQ
from ict_live.engine import outcomes as OUT
from ict_live.engine import pipeline
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod

OUT_PATH = "ict_live/research/datasets/exit_model_records.jsonl"
RESULT_MD = "ict_live/research/RESULT_exit_models.md"
MODELS = ["fixed_2R", "fixed_3R", "be_after_1R", "partial_runner", "structural_target"]
_TF_MIN_STOP = 2.0


def _fill_index(bars, decision_index, entry, horizon):
    end = min(len(bars), decision_index + horizon + 1)
    for j in range(decision_index, end):
        if bars[j].low <= entry <= bars[j].high:
            return j
    return None


def realized_r(bars, decision_index, *, direction, entry, stop, target,
               horizon_bars=OUT.DEFAULT_HORIZON_BARS) -> dict:
    """Return {model: (result_str, R or None)} for the five pre-registered exit models."""
    long = direction == "long"
    risk = abs(entry - stop)
    out = {m: ("INVALID", None) for m in MODELS}
    if risk <= 0:
        return out
    fill = _fill_index(bars, decision_index, entry, horizon_bars)
    if fill is None:
        return {m: ("NO_FILL", None) for m in MODELS}
    end = min(len(bars) - 1, fill + horizon_bars)
    reward_R = (abs(target - entry) / risk) if target is not None else None

    def hit_stop(b, lvl):
        return b.low <= lvl if long else b.high >= lvl

    def hit_fav(b, lvl):                              # favorable level reached
        return b.high >= lvl if long else b.low <= lvl

    def lvl(nR):
        return entry + nR * risk if long else entry - nR * risk

    def horizon_mark():
        c = bars[end].close
        return round(((c - entry) if long else (entry - c)) / risk, 3)

    # --- fixed NR ---
    def fixed(n):
        tgt = lvl(n)
        for j in range(fill, end + 1):
            b = bars[j]
            s, t = hit_stop(b, stop), hit_fav(b, tgt)
            if s and t:
                return ("AMBIGUOUS", None)
            if t:
                return ("TARGET", float(n))
            if s:
                return ("STOP", -1.0)
        return ("HORIZON", horizon_mark())
    out["fixed_2R"] = fixed(2)
    out["fixed_3R"] = fixed(3)

    # --- structural target (baseline) ---
    if target is None:
        out["structural_target"] = ("NO_TARGET", None)
    else:
        res = None
        for j in range(fill, end + 1):
            b = bars[j]
            s, t = hit_stop(b, stop), hit_fav(b, target)
            if s and t:
                res = ("AMBIGUOUS", None); break
            if t:
                res = ("TARGET", round(reward_R, 3)); break
            if s:
                res = ("STOP", -1.0); break
        out["structural_target"] = res or ("HORIZON", horizon_mark())

    # --- BE after 1R (target = structural) ---
    if target is not None:
        one = lvl(1)
        phase, res = 1, None
        for j in range(fill, end + 1):
            b = bars[j]
            if phase == 1:
                s, t1, t = hit_stop(b, stop), hit_fav(b, one), hit_fav(b, target)
                if s and (t1 or t):
                    res = ("AMBIGUOUS", None); break
                if t:
                    res = ("TARGET", round(reward_R, 3)); break
                if t1:
                    phase = 2; continue
                if s:
                    res = ("STOP", -1.0); break
            else:                                     # stop at breakeven (entry)
                bs, t = hit_stop(b, entry), hit_fav(b, target)
                if bs and t:
                    res = ("AMBIGUOUS", None); break
                if t:
                    res = ("TARGET", round(reward_R, 3)); break
                if bs:
                    res = ("BE", 0.0); break
        out["be_after_1R"] = res or ("HORIZON", horizon_mark())

    # --- partial + runner (50% at +2R, BE on remainder, runner to structural target) ---
    if target is not None:
        two = lvl(2)
        phase, res = 1, None
        for j in range(fill, end + 1):
            b = bars[j]
            if phase == 1:
                s, t2, t = hit_stop(b, stop), hit_fav(b, two), hit_fav(b, target)
                if s and (t2 or t):
                    res = ("AMBIGUOUS", None); break
                if t:
                    res = ("TARGET", round(reward_R, 3)); break     # ran all the way first
                if t2:
                    phase = 2; continue
                if s:
                    res = ("STOP", -1.0); break
            else:
                bs, t = hit_stop(b, entry), hit_fav(b, target)
                if bs and t:
                    res = ("AMBIGUOUS", None); break
                if t:
                    res = ("PARTIAL+TARGET", round(1.0 + 0.5 * reward_R, 3)); break
                if bs:
                    res = ("PARTIAL+BE", 1.0); break
        if res is None:
            res = (("PARTIAL+HORIZON", round(1.0 + 0.5 * horizon_mark(), 3)) if phase == 2
                   else ("HORIZON", horizon_mark()))
        out["partial_runner"] = res
    return out


def generate(symbols=("MES", "MNQ"), *, signal_tf="1H", entry_tf="15m", dev_start="2019-05-01",
             dev_end="2025-01-01", stride=6, window=240,
             horizon_bars=OUT.DEFAULT_HORIZON_BARS, out_path=OUT_PATH, allow_oos=False) -> dict:
    assert allow_oos or dev_end <= "2025-01-01", "dev_end must not enter the locked OOS"
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
                r = realized_r(sig, k, direction=win.direction, entry=win.entry, stop=win.stop,
                               target=win.target, horizon_bars=horizon_bars)
                rows.append({"scene_id": f"{symbol}:{seg.contract}:{signal_tf}:{sig[k].open_time.isoformat()}",
                             "symbol": symbol, "execution": EQ.assess(ms).execution,
                             "reward_R": round(win.rr, 3),
                             "models": {m: {"result": r[m][0], "R": r[m][1]} for m in MODELS}})
        print(f"  {symbol}: {sum(1 for r in rows if r['symbol'] == symbol)} trades")
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("\n".join(json.dumps(r, default=str) for r in rows))
    return {"symbols": list(symbols), "trades": len(rows), "out_path": out_path}


def _agg_model(rows, model, execution=None):
    sel = [r for r in rows if execution is None or r["execution"] == execution]
    Rs = [r["models"][model]["R"] for r in sel if r["models"][model]["R"] is not None]
    amb = sum(1 for r in sel if r["models"][model]["result"] in ("AMBIGUOUS", "NO_FILL", "NO_TARGET", "INVALID"))
    if not Rs:
        return {"n": len(sel), "scored": 0}
    Rs_sorted = sorted(Rs)
    wins = sum(1 for x in Rs if x > 0)
    capped = [min(x, 5.0) for x in Rs]
    top5 = sum(sorted(Rs, reverse=True)[:5])
    total = sum(Rs)
    return {"n": len(sel), "scored": len(Rs), "excluded": amb,
            "win_rate": round(wins / len(Rs), 3),
            "expectancy_R": round(total / len(Rs), 3),
            "median_R": round(Rs_sorted[len(Rs_sorted) // 2], 3),
            "total_R": round(total, 1),
            "expectancy_capped5_R": round(sum(capped) / len(capped), 3),
            "top5_share_pct": (round(100 * top5 / total, 0) if total > 0 else None)}


def analyze(rows, execution=None) -> dict:
    return {m: _agg_model(rows, m, execution) for m in MODELS}


def render_md(rows) -> str:
    def table(execution, title):
        a = analyze(rows, execution)
        L = [f"## {title}", "",
             "| model | scored | win rate | expectancy R | median R | capped@5R | total R | top-5 share |",
             "|---|---|---|---|---|---|---|---|"]
        for m in MODELS:
            x = a[m]
            if not x.get("scored"):
                L.append(f"| {m} | 0 | — | — | — | — | — | — |"); continue
            L.append(f"| {m} | {x['scored']} | {x['win_rate']} | {x['expectancy_R']} | {x['median_R']} | "
                     f"{x['expectancy_capped5_R']} | {x['total_R']} | {x['top5_share_pct']}% |")
        return "\n".join(L)
    n = len(rows)
    return ("# Exit-model characterization (dev, engine frozen; pre-registered, no optimization)\n\n"
            f"Trades: {n} distinct engine recommendations (MES+MNQ dev). Metrics are descriptive.\n\n"
            + table(None, "All engine recommendations") + "\n\n"
            + table("TRADE", "v1 TRADE subset") + "\n\n"
            + table("PASS", "v1 PASS subset") + "\n\n"
            "*capped@5R* = expectancy with each trade capped at +5R (fat-tail robustness); "
            "*top-5 share* = % of total R from the 5 biggest winners (fragility).\n")


if __name__ == "__main__":
    rep = generate()
    rows = [json.loads(l) for l in Path(OUT_PATH).read_text().splitlines() if l.strip()]
    Path(RESULT_MD).write_text(render_md(rows))
    print(json.dumps({**rep, "all": analyze(rows)}, indent=1, default=str))
