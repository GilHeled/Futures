"""WHY does price stall around +2R? — a characterization study (diagnostics only; engine frozen).

Discriminates four hypotheses for the ~2R median MFE:
  H1 structural-complete  — the stall is a real opposing turning point (a new pivot forms there).
  H2 opposing-liquidity   — the nearest opposing structural liquidity sits ~2R ahead.
  H3 volatility-exhaust   — ~2R is just a characteristic volatility unit (fixed ATR, not fixed R).
  H4 late-entry-geometry  — the FVG-CE entry sits a fixed fraction into the displacement, so simply
                            RETURNING to the impulse high (displacement end) already equals ~2R.

Key geometric identity: entry = FVG CE inside the displacement; stop = manipulation extreme ≈
displacement start. So risk = entry − disp_start, and the distance from entry back to the impulse
high (disp_end) is (disp_size − risk) = (disp_size_R − 1)·R. If the entry sits ~1/3 into the leg
(disp_size_R ≈ 3), returning to the impulse high is +2R BY CONSTRUCTION. This module measures whether
that is what happens (H4/H1) versus price stalling at independent liquidity (H2) or a fixed ATR (H3).

Run under the venv: .venv/bin/python -m ict_live.journal.stall_analysis
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import execution_quality as EQ
from ict_live.engine import outcomes as OUT
from ict_live.engine import pipeline
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod

OUT_PATH = "ict_live/research/datasets/stall_records.jsonl"
RESULT_MD = "ict_live/research/RESULT_stall_analysis.md"
_TF_MIN_STOP = 2.0
_W = 2                                    # fractal width for the pivot test


def _atr(bars, k, period=14):
    if k < 1:
        return None
    trs = []
    for j in range(max(1, k - period + 1), k + 1):
        h, l, pc = bars[j].high, bars[j].low, bars[j - 1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return (sum(trs) / len(trs)) if trs else None


def _fill_index(bars, k, entry, horizon):
    for j in range(k, min(len(bars), k + horizon + 1)):
        if bars[j].low <= entry <= bars[j].high:
            return j
    return None


def measure(ms, sig, k, *, horizon=OUT.DEFAULT_HORIZON_BARS) -> dict | None:
    win = ms.recommendation.setup
    if win is None:
        return None
    long = win.direction == "long"
    s = 1.0 if long else -1.0
    entry, stop = win.entry, win.stop
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    fvg = EQ._by_dep(ms.ranked_fvgs, win.depends_on, "FVG")
    disp = EQ._by_dep(ms.ranked_displacements, fvg.depends_on, "DISP") if fvg else None
    if disp is None:
        return None
    d_start, d_end = disp.start_price, disp.end_price

    fill = _fill_index(sig, k, entry, horizon)
    if fill is None:
        return None
    end = min(len(sig) - 1, fill + horizon)

    # favorable extreme (MFE) over the realized life
    ext, ext_bar = 0.0, fill
    for j in range(fill, end + 1):
        fav = s * ((sig[j].high if long else sig[j].low) - entry)
        if fav > ext:
            ext, ext_bar = fav, j
    mfe_R = ext / risk

    def fav(p):
        return s * (p - entry)
    disp_end_fav = fav(d_end)                              # favorable distance entry->impulse high
    leg = s * (d_end - d_start)                            # signed displacement size (>0)
    entry_from_start = s * (entry - d_start)
    entry_frac_f = (entry_from_start / leg) if leg else None
    disp_size_R = abs(d_end - d_start) / risk

    # did the MFE stall at the displacement high? (1.0 == exactly at impulse high)
    mfe_vs_dispEnd = (ext / disp_end_fav) if disp_end_fav > 0 else None

    atr = _atr(sig, k)
    # nearest OPPOSING structural liquidity ahead (draw side: highs for long, lows for short)
    draw_kind = "high" if long else "low"
    ahead = [fav(sw.price) / risk for sw in ms.structural
             if sw.kind == draw_kind and fav(sw.price) > 0]
    dist_opp_liq_R = min(ahead) if ahead else None

    # H1: is the MFE bar a genuine opposing pivot? (local favorable extreme, not exceeded for _W bars)
    mfe_is_pivot = None
    if ext_bar + _W <= end and ext_bar - _W >= fill - _W:
        window = sig[max(0, ext_bar - _W): ext_bar + _W + 1]
        peak = max((s * ((b.high if long else b.low) - entry)) for b in window)
        after = max((s * ((b.high if long else b.low) - entry))
                    for b in sig[ext_bar + 1: ext_bar + _W + 1])
        mfe_is_pivot = (ext >= peak - 1e-9) and (after < ext - 1e-9)

    return {
        "mfe_R": round(mfe_R, 3),
        "reward_R": round(win.rr, 3),
        "disp_size_R": round(disp_size_R, 3),
        "entry_frac_f": round(entry_frac_f, 3) if entry_frac_f is not None else None,
        "dist_entry_to_dispEnd_R": round(disp_end_fav / risk, 3),
        "mfe_vs_dispEnd": round(mfe_vs_dispEnd, 3) if mfe_vs_dispEnd is not None else None,
        "stop_is_disp_start": abs(stop - d_start) <= 1e-6,
        "risk_ATR": round(risk / atr, 3) if atr else None,
        "mfe_ATR": round(ext / atr, 3) if atr else None,
        "dist_opp_liq_R": round(dist_opp_liq_R, 3) if dist_opp_liq_R is not None else None,
        "mfe_is_pivot": mfe_is_pivot,
    }


def generate(symbols=("MES", "MNQ"), *, signal_tf="1H", entry_tf="15m", dev_start="2019-05-01",
             dev_end="2025-01-01", stride=6, window=240, out_path=OUT_PATH) -> dict:
    assert dev_end <= "2025-01-01", "dev_end must not enter the locked OOS"
    rows = []
    for symbol in symbols:
        bars5 = data_mod.load_5m(symbol, start=dev_start, end=dev_end)
        for si, seg in enumerate(rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, symbol))):
            if si == 0:
                continue
            sig = data_mod.resample(seg.bars, signal_tf)
            ref_all = data_mod.resample(seg.bars, entry_tf)
            seen = set()
            for k in range(window, len(sig), stride):
                cc = sig[k].close_time
                ms = pipeline.analyze(sig[max(0, k - window + 1):k + 1], signal_tf,
                                      refine_bars=[b for b in ref_all if b.close_time <= cc],
                                      min_stop=_TF_MIN_STOP)
                win = ms.recommendation.setup
                if win is None or (seg.contract, win.id) in seen:
                    continue
                seen.add((seg.contract, win.id))
                m = measure(ms, sig, k)
                if m is not None:
                    rows.append({"symbol": symbol, "execution": EQ.assess(ms).execution, **m})
        print(f"  {symbol}: {sum(1 for r in rows if r['symbol'] == symbol)} trades")
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text("\n".join(json.dumps(r, default=str) for r in rows))
    return {"trades": len(rows), "out_path": out_path}


def _q(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {}
    def qq(p):
        return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2)
    return {"n": len(xs), "p25": qq(.25), "median": qq(.5), "p75": qq(.75)}


def analyze(rows) -> dict:
    import numpy as np
    def col(name):
        return [r[name] for r in rows if r.get(name) is not None]
    mfe = np.array(col("mfe_R"))
    # H3: is MFE_price ~ risk (structural proportionality) or ~ ATR (volatility)?  use R- and ATR-space
    def corr(a, b):
        a, b = np.array(a), np.array(b)
        return round(float(np.corrcoef(a, b)[0, 1]), 3) if len(a) > 2 and a.std() and b.std() else None
    pit = [r for r in rows if r.get("mfe_is_pivot") is not None]
    stalled_at_high = [r for r in rows if r.get("mfe_vs_dispEnd") is not None]
    within = sum(1 for r in stalled_at_high if 0.8 <= r["mfe_vs_dispEnd"] <= 1.2)
    exceeded = sum(1 for r in stalled_at_high if r["mfe_vs_dispEnd"] > 1.2)
    below = sum(1 for r in stalled_at_high if r["mfe_vs_dispEnd"] < 0.8)
    return {
        "n": len(rows),
        "mfe_R": _q(col("mfe_R")),
        "disp_size_R": _q(col("disp_size_R")),
        "entry_frac_f": _q(col("entry_frac_f")),
        "dist_entry_to_dispEnd_R": _q(col("dist_entry_to_dispEnd_R")),
        "mfe_vs_dispEnd": _q(col("mfe_vs_dispEnd")),
        "dist_opp_liq_R": _q(col("dist_opp_liq_R")),
        "risk_ATR": _q(col("risk_ATR")),
        "mfe_ATR": _q(col("mfe_ATR")),
        "stop_is_disp_start_pct": round(100 * sum(1 for r in rows if r.get("stop_is_disp_start")) / len(rows), 0),
        "mfe_is_pivot_pct": (round(100 * sum(1 for r in pit if r["mfe_is_pivot"]) / len(pit), 0) if pit else None),
        "stall_vs_impulse_high": {"below_0.8": below, "at_0.8-1.2": within, "above_1.2": exceeded,
                                  "n": len(stalled_at_high)},
        "corr_mfeR_dispSizeR": corr(col_pair(rows, "mfe_R", "disp_size_R")[0], col_pair(rows, "mfe_R", "disp_size_R")[1]),
        "corr_mfeATR_riskATR": corr(col_pair(rows, "mfe_ATR", "risk_ATR")[0], col_pair(rows, "mfe_ATR", "risk_ATR")[1]),
    }


def col_pair(rows, a, b):
    xa, xb = [], []
    for r in rows:
        if r.get(a) is not None and r.get(b) is not None:
            xa.append(r[a]); xb.append(r[b])
    return xa, xb


def render_md(rows) -> str:
    a = analyze(rows)
    L = ["# Why does price stall ~2R? — characterization (dev, engine frozen; diagnostics only)", "",
         f"Trades: {a['n']}. stop == displacement start (manipulation extreme) in {a['stop_is_disp_start_pct']}% of trades.", "",
         "## Distributions (p25 / median / p75)", "",
         "| metric | p25 | median | p75 |", "|---|---|---|---|"]
    for m in ("mfe_R", "disp_size_R", "entry_frac_f", "dist_entry_to_dispEnd_R", "mfe_vs_dispEnd",
              "dist_opp_liq_R", "risk_ATR", "mfe_ATR"):
        q = a[m]
        L.append(f"| {m} | {q.get('p25')} | {q.get('median')} | {q.get('p75')} |")
    sv = a["stall_vs_impulse_high"]
    L += ["", "## Where does the MFE stall relative to the impulse high (displacement end)?", "",
          f"- below it (<0.8×): {sv['below_0.8']}",
          f"- AT it (0.8–1.2×): **{sv['at_0.8-1.2']}**  of {sv['n']}",
          f"- beyond it (>1.2×): {sv['above_1.2']}",
          "", "## H1 — is the stall a genuine opposing pivot?",
          f"- MFE bar forms an opposing pivot in **{a['mfe_is_pivot_pct']}%** of trades",
          "", "## H3 — proportionality vs volatility",
          f"- corr(MFE_R, disp_size_R) = {a['corr_mfeR_dispSizeR']}  (structural proportionality if high)",
          f"- corr(MFE_ATR, risk_ATR) = {a['corr_mfeATR_riskATR']}", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    rep = generate()
    rows = [json.loads(l) for l in Path(OUT_PATH).read_text().splitlines() if l.strip()]
    Path(RESULT_MD).write_text(render_md(rows))
    print(json.dumps({**rep, "analysis": analyze(rows)}, indent=1, default=str))
