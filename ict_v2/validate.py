"""Historical setup ENUMERATOR — for validating v2 against charts (offline; reads cached Databento 5m).

Live-watching catches very few setups (most FVGs mitigate before you look). This replays the v2 cascade
over a PAST window and lists every setup at the moment it became valid, with ET timestamps + prices, so
you can pull each one up on a chart and check the Structure / Quality / Recommendation by hand.

5-minute is the finest cached TF, so the cascade runs 4H context → 1H setup → 15m confirmation → 5m
trigger (5m is a course-legitimate execution TF). This is a REVIEW AID, not a backtest or edge claim —
no PnL, no tuning.

    python -m ict_v2.validate --symbol MES --start 2025-06-02 --end 2025-06-13
    python -m ict_v2.validate --symbol MNQ --start 2025-06-02 --end 2025-06-13 --relaxed --stage confirmation
"""
from __future__ import annotations

import argparse
import bisect
from datetime import timedelta

from ict_live.research import data as D
from ict_v2 import pipeline as P, recommend as REC

_LOOKBACK_DAYS = 12          # history each cursor sees (enough for a 4H dealing range + context)


def _stage_cands(ctx_bars, su_bars, cf_bars, tr_bars, stage):
    """Run ONLY the cascade layers needed for `stage` (cheaper than the full analyze_mtf per cursor)."""
    ctx = P.htf_context(ctx_bars, "4H")
    setup = P.mtf_setup(su_bars, "1H", ctx)
    if stage == "setup":
        return setup.cand_info
    conf = P.confirm_setup(cf_bars, "15m", ctx, setup)
    if stage == "confirmation":
        return conf.cand_info
    return P.execution_for(tr_bars, "5m", ctx, setup, conf).cand_info


def enumerate_setups(symbol, start, end, *, stage="setup", relaxed=False,
                     want=("TAKE",), step_min=60):
    """Replay the cascade at each `step_min` cursor in [start, end]; return de-duplicated setups on
    `stage` (setup/confirmation/execution) whose recommendation is in `want` (default TAKE).
    Each setup is reported once, at the cursor it first appears."""
    if relaxed:
        REC.configure(min_rr=1.0, killzone=False, require_retrace=False)
    import pandas as pd
    pad = (pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    bars5 = D.load_5m(symbol, start=pad, end=end)
    if not bars5:
        return []
    times = [b.open_time for b in bars5]                      # chronological → bisect for O(log n) slicing
    win_start = pd.Timestamp(start, tz="UTC").to_pydatetime()
    win_end = pd.Timestamp(end, tz="UTC").to_pydatetime()
    seen, out = set(), []
    cur, step, lookback = win_start, timedelta(minutes=step_min), timedelta(days=_LOOKBACK_DAYS)
    while cur <= win_end:
        i0 = bisect.bisect_left(times, cur - lookback)
        i1 = bisect.bisect_right(times, cur)
        slc = bars5[i0:i1]
        if len(slc) >= 60:
            ctx = D.resample(slc, "4H"); su = D.resample(slc, "1H"); cf = D.resample(slc, "15m")
            for c in _stage_cands(ctx, su, cf, slc, stage):
                if c["recommendation"] not in want:
                    continue
                # de-dup ACROSS cursors by PRICE (ids are index-based and shift as the window slides,
                # so the same real setup would otherwise repeat at every cursor). entry+stop are stable.
                key = (c["direction"], c["entry"], c["stop"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({"time": cur, **c})
        cur += step
    if relaxed:
        REC.configure(min_rr=2.0, killzone=True, require_retrace=True)   # restore faithful defaults
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MES", help="MES / MNQ / MYM / M2K (cached Databento 5m)")
    ap.add_argument("--start", required=True, help="ISO date (UTC), inclusive")
    ap.add_argument("--end", required=True, help="ISO date (UTC), inclusive")
    ap.add_argument("--stage", default="setup", choices=["setup", "confirmation", "execution"])
    ap.add_argument("--relaxed", action="store_true", help="drop killzone + retrace, RR floor=1 (see more)")
    ap.add_argument("--want", default="TAKE", help="comma list of recommendations to list (TAKE,SKIP,WATCH)")
    ap.add_argument("--step-min", type=int, default=15)
    a = ap.parse_args()
    want = tuple(x.strip().upper() for x in a.want.split(","))
    rows = enumerate_setups(a.symbol, a.start, a.end, stage=a.stage, relaxed=a.relaxed,
                            want=want, step_min=a.step_min)
    mode = "RELAXED (killzone/retrace off, RR≥1)" if a.relaxed else "FAITHFUL (killzone/retrace on, RR≥2)"
    print(f"# {a.symbol}  {a.stage} candidates  {a.start}→{a.end}  [{mode}]  want={','.join(want)}")
    print(f"# {len(rows)} setup(s). 5m data → cascade 4H/1H/15m/5m. Pull each time (ET) up on a chart.\n")
    if not rows:
        print("  (none — try --relaxed, a wider window, another --stage, or --want TAKE,WATCH)")
        return
    print(f"  {'time (ET)':17} {'dir':5} {'rec':5} {'entry':>9} {'stop':>9} {'target':>9} {'RR':>6}  "
          f"{'align':16} {'sess':6} leg")
    for r in rows:
        lg = r.get("leg") or {}
        legs = f"{'▲' if lg.get('dir')=='bullish' else '▼'} {lg.get('from')}→{lg.get('to')}" if lg else "—"
        flt = "" if r["recommendation"] == "TAKE" else " | " + "; ".join(r.get("reasons") or [])[:1]
        print(f"  {r['time'].astimezone(D.ET).strftime('%Y-%m-%d %H:%M'):17} {r['direction']:5} "
              f"{r['recommendation']:5} {str(r['entry']):>9} {str(r['stop']):>9} {str(r['target']):>9} "
              f"{str(r['rr']):>6}  {r['context_label']:16} {r.get('session') or '-':6} {legs}{flt}")


if __name__ == "__main__":
    main()
