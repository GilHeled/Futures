"""Is the manipulation-extreme stop a genuine invalidation point, or systematically too tight?

For every filled trade, split the excursion around the FIRST −1R stop touch and classify each STOPPED
trade into exactly one bucket:
  1. true_invalidation   — stopped, and price never meaningfully resumes the original direction.
  2. premature_stop      — stopped, then price resumes the thesis SHORTLY after (recovery ≤ 12 bars).
  3. late_continuation   — stopped, then price resumes only much later (recovery > 12 bars) — likely
                           unrelated new structure, not the original setup.

"Meaningful resume / recovery" = favorable excursion reaches **+2R** after the stop. "Shortly" = the
recovery occurs within **12 bars** of the stop. Both thresholds are FIXED and pre-declared (no sweep).
To gauge whether a modestly wider stop would have preserved the thesis WITHOUT optimizing a multiple,
we report the adverse depth (MAE, in R) price actually reached before recovering, and — as a single
illustrative reference, not a tuned parameter — the share of recovered trades whose drawdown stayed
within 1.5R.

Engine frozen; diagnostics only. Run under the venv: .venv/bin/python -m ict_live.journal.stop_analysis
"""
from __future__ import annotations

import json
from pathlib import Path

from ict_live.engine import execution_quality as EQ
from ict_live.engine import outcomes as OUT
from ict_live.engine import pipeline
from ict_live.research import data as data_mod
from ict_live.research import rolls as rolls_mod

OUT_PATH = "ict_live/research/datasets/stop_records.jsonl"
RESULT_MD = "ict_live/research/RESULT_stop_analysis.md"
_TF_MIN_STOP = 2.0
RECOVER_R = 2.0            # favorable level that counts as "resuming the thesis"
SHORT_BARS = 12           # recovery within this many bars of the stop = "shortly" (premature)
WIDER_REF_R = 1.5         # single illustrative reference for "would a modestly wider stop preserve?"
MEANINGFUL_R = 2.0        # a trade "produces a meaningful favorable move" if eventual MFE >= this


def _fill_index(bars, k, entry, horizon):
    for j in range(k, min(len(bars), k + horizon + 1)):
        if bars[j].low <= entry <= bars[j].high:
            return j
    return None


def measure_stop(sig, k, *, direction, entry, stop, target, horizon=OUT.DEFAULT_HORIZON_BARS):
    long = direction == "long"
    s = 1.0 if long else -1.0
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    fill = _fill_index(sig, k, entry, horizon)
    if fill is None:
        return None
    end = min(len(sig) - 1, fill + horizon)

    def fav(p):
        return s * (p - entry) / risk                       # favorable excursion in R
    def adv(p):
        return s * (entry - p) / risk                       # adverse excursion in R
    def fav_bar(j):
        return fav(sig[j].high if long else sig[j].low)
    def adv_bar(j):
        return adv(sig[j].low if long else sig[j].high)
    def tgt_bar(j):
        return (sig[j].high >= target) if long else (sig[j].low <= target)

    # eventual MFE over the full horizon
    ext, ext_bar = 0.0, fill
    for j in range(fill, end + 1):
        if fav_bar(j) > ext:
            ext, ext_bar = fav_bar(j), j
    eventual_mfe_R = round(ext, 3)

    # first −1R stop touch
    t_stop = next((j for j in range(fill, end + 1)
                   if (sig[j].low <= stop if long else sig[j].high >= stop)), None)
    stopped = t_stop is not None

    def mfe_over(a, b):
        return round(max((fav_bar(j) for j in range(a, b + 1)), default=0.0), 3)

    rec = {"stopped": stopped, "eventual_mfe_R": eventual_mfe_R,
           "t_entry_to_stop": None, "mfe_before_stop": None, "mfe_after_stop": None,
           "t_stop_to_mfe": None, "recovered": None, "t_stop_to_recovery": None,
           "target_after_stop": None, "mae_before_recovery_R": None, "bucket": "survived"}
    if not stopped:
        rec["mfe_before_stop"] = eventual_mfe_R
        return rec

    rec["t_entry_to_stop"] = t_stop - fill
    rec["mfe_before_stop"] = mfe_over(fill, t_stop)
    rec["mfe_after_stop"] = mfe_over(t_stop + 1, end) if t_stop < end else 0.0
    rec["t_stop_to_mfe"] = ext_bar - t_stop if ext_bar > t_stop else 0
    rec["target_after_stop"] = (target is not None
                                and any(tgt_bar(j) for j in range(t_stop + 1, end + 1)))
    rec_bar = next((j for j in range(t_stop + 1, end + 1) if fav_bar(j) >= RECOVER_R), None)
    recovered = rec_bar is not None
    rec["recovered"] = recovered
    if recovered:
        rec["t_stop_to_recovery"] = rec_bar - t_stop
        rec["mae_before_recovery_R"] = round(max(adv_bar(j) for j in range(fill, rec_bar + 1)), 3)
        rec["bucket"] = "premature_stop" if (rec_bar - t_stop) <= SHORT_BARS else "late_continuation"
    else:
        rec["bucket"] = "true_invalidation"
    return rec


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
                m = measure_stop(sig, k, direction=win.direction, entry=win.entry, stop=win.stop,
                                 target=win.target)
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
    stopped = [r for r in rows if r["stopped"]]
    meaningful = [r for r in stopped if r["eventual_mfe_R"] >= MEANINGFUL_R]
    def counts(sub):
        b = {"true_invalidation": 0, "premature_stop": 0, "late_continuation": 0}
        for r in sub:
            b[r["bucket"]] = b.get(r["bucket"], 0) + 1
        return b
    prem = [r for r in stopped if r["bucket"] == "premature_stop"]
    return {
        "n": len(rows), "n_stopped": len(stopped), "n_survived": len(rows) - len(stopped),
        "n_stopped_meaningful": len(meaningful),
        "buckets_all_stopped": counts(stopped),
        "buckets_meaningful": counts(meaningful),
        "mfe_before_stop": _q([r["mfe_before_stop"] for r in stopped]),
        "mfe_after_stop": _q([r["mfe_after_stop"] for r in stopped]),
        "t_entry_to_stop": _q([r["t_entry_to_stop"] for r in stopped]),
        "t_stop_to_mfe": _q([r["t_stop_to_mfe"] for r in stopped]),
        "target_after_stop_rate": (round(sum(1 for r in stopped if r["target_after_stop"]) / len(stopped), 3)
                                   if stopped else None),
        "premature_mae_before_recovery": _q([r["mae_before_recovery_R"] for r in prem]),
        "premature_within_1.5R": (round(sum(1 for r in prem if (r["mae_before_recovery_R"] or 9) <= WIDER_REF_R) / len(prem), 3)
                                  if prem else None),
    }


def render_md(rows) -> str:
    a = analyze(rows)
    bs, bm = a["buckets_all_stopped"], a["buckets_meaningful"]
    ns, nm = a["n_stopped"], a["n_stopped_meaningful"]
    def pct(x, n):
        return f"{x} ({round(100 * x / n)}%)" if n else str(x)
    L = ["# Is the manipulation-extreme stop genuine invalidation or too tight? (dev, engine frozen)", "",
         f"- trades {a['n']} · stopped {a['n_stopped']} · survived (never −1R) {a['n_survived']}",
         f"- of stopped, eventual MFE ≥ {MEANINGFUL_R}R (a real move happened): {a['n_stopped_meaningful']}",
         "", "## Bucket classification of STOPPED trades", "",
         "| bucket | all stopped | stopped w/ meaningful move |", "|---|---|---|",
         f"| true_invalidation | {pct(bs['true_invalidation'], ns)} | {pct(bm['true_invalidation'], nm)} |",
         f"| premature_stop (≤{SHORT_BARS} bars) | {pct(bs['premature_stop'], ns)} | {pct(bm['premature_stop'], nm)} |",
         f"| late_continuation (>{SHORT_BARS} bars) | {pct(bs['late_continuation'], ns)} | {pct(bm['late_continuation'], nm)} |",
         "", "## Excursion split around the first −1R stop (p25/median/p75)", "",
         f"- MFE before stop: {a['mfe_before_stop']}",
         f"- MFE after stop: {a['mfe_after_stop']}",
         f"- bars entry→stop: {a['t_entry_to_stop']}",
         f"- bars stop→eventual MFE: {a['t_stop_to_mfe']}",
         f"- original target eventually reached after the stop: **{a['target_after_stop_rate']}**",
         "", "## Would a modestly wider stop have preserved the thesis? (premature-stop trades)", "",
         f"- adverse depth reached before recovery (R): {a['premature_mae_before_recovery']}",
         f"- share whose drawdown stayed within {WIDER_REF_R}R (illustrative reference, not tuned): "
         f"**{a['premature_within_1.5R']}**", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    rep = generate()
    rows = [json.loads(l) for l in Path(OUT_PATH).read_text().splitlines() if l.strip()]
    Path(RESULT_MD).write_text(render_md(rows))
    print(json.dumps({**rep, "analysis": analyze(rows)}, indent=1, default=str))
