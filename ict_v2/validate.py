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


def _bars_for(tf, slc):
    """Bars for a timeframe from the 5m slice: resample for 15m/1H/4H, the raw slice for 5m."""
    return slc if tf == "5m" else D.resample(slc, tf)


def _stage_cands(slc, stage, tfs):
    """Run ONLY the cascade layers needed for `stage` (cheaper than the full analyze_mtf per cursor).
    `tfs` = (context, setup, confirm, trigger) timeframe labels."""
    ctf, stf, cftf, ttf = tfs
    ctx = P.htf_context(_bars_for(ctf, slc), ctf)
    setup = P.mtf_setup(_bars_for(stf, slc), stf, ctx)
    if stage == "setup":
        return setup.cand_info
    conf = P.confirm_setup(_bars_for(cftf, slc), cftf, ctx, setup)
    if stage == "confirmation":
        return conf.cand_info
    return P.execution_for(_bars_for(ttf, slc), ttf, ctx, setup, conf).cand_info


def enumerate_setups(symbol, start, end, *, stage="setup", relaxed=False,
                     want=("TAKE",), step_min=60, tfs=("4H", "1H", "15m", "5m")):
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
            for c in _stage_cands(slc, stage, tfs):
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


def _simulate_one(row, bars_after, *, fill_bars=48, horizon_bars=288):
    """Simulate ONE setup on the 5m bars that follow it: rest a limit at the entry (FVG CE), fill on
    the retrace, then run to stop or target. Returns R (reward multiple; -1 = full stop) or None if it
    never fills / invalidates pre-fill. Conservative tie-break: a bar touching BOTH stop and target is
    a LOSS. `fill_bars` = how long the limit rests; `horizon_bars` = max hold (288×5m = 24h)."""
    d, entry, stop, target = row["direction"], row["entry"], row["stop"], row["target"]
    if None in (entry, stop, target):
        return None
    risk = abs(stop - entry)
    if risk <= 0:
        return None
    rmult = abs(target - entry) / risk
    filled = False
    for i, b in enumerate(bars_after):
        if not filled:
            if i >= fill_bars:
                return None                                  # limit expired unfilled
            touched = (b.low <= entry <= b.high)
            if not touched:
                continue
            filled = True                                    # fill on this bar; evaluate outcome from here on
        hit_stop = (b.high >= stop) if d == "short" else (b.low <= stop)
        hit_tgt = (b.low <= target) if d == "short" else (b.high >= target)
        if hit_stop and hit_tgt:
            return -1.0                                       # ambiguous same bar → assume stop (pessimistic)
        if hit_stop:
            return -1.0
        if hit_tgt:
            return rmult
        if filled and i >= horizon_bars:
            return (entry - b.close) / risk if d == "short" else (b.close - entry) / risk   # time exit
    return None


def simulate(symbol, start, end, *, tfs=("4H", "15m", "5m", "5m"), stage="setup",
             relaxed_retrace=True, min_rr=2.0, killzone=True):
    """Enumerate setups then simulate each. Returns (results, matched_random) — lists of R. Setups use
    the faithful ≥min_rr + killzone filters but count ARMED (retrace off) as tradeable (we simulate the
    actual fill). matched_random flips each setup's direction (same fills) as a no-skill control."""
    REC.configure(min_rr=min_rr, killzone=killzone, require_retrace=not relaxed_retrace)
    rows = enumerate_setups(symbol, start, end, stage=stage, want=("TAKE",),
                            step_min=15, tfs=tfs)
    REC.configure(min_rr=2.0, killzone=True, require_retrace=True)
    import pandas as pd
    pad = (pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    bars5 = D.load_5m(symbol, start=pad, end=(pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=5)).strftime("%Y-%m-%d"))
    times = [b.open_time for b in bars5]
    res, rnd = [], []
    for r in rows:
        j = bisect.bisect_right(times, r["time"])
        after = bars5[j:j + 400]
        R = _simulate_one(r, after)
        if R is None:
            continue
        res.append(R)
        flip = dict(r); flip["direction"] = "long" if r["direction"] == "short" else "short"
        flip["stop"], flip["target"] = 2 * r["entry"] - r["stop"], 2 * r["entry"] - r["target"]   # mirror geometry
        Rr = _simulate_one(flip, after)
        rnd.append(Rr if Rr is not None else 0.0)
    return res, rnd


def _agg(rs):
    if not rs:
        return "n=0"
    n = len(rs); wins = [x for x in rs if x > 0]; losses = [x for x in rs if x <= 0]
    tot = sum(rs); win = 100 * len(wins) / n
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf")
    return (f"n={n}  win%={win:.0f}  avgR={tot/n:+.2f}  totalR={tot:+.1f}  "
            f"PF={pf:.2f}  best={max(rs):+.1f} worst={min(rs):+.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="MES", help="MES / MNQ / MYM / M2K (cached Databento 5m)")
    ap.add_argument("--start", required=True, help="ISO date (UTC), inclusive")
    ap.add_argument("--end", required=True, help="ISO date (UTC), inclusive")
    ap.add_argument("--stage", default="setup", choices=["setup", "confirmation", "execution"])
    ap.add_argument("--relaxed", action="store_true", help="drop killzone + retrace, RR floor=1 (see more)")
    ap.add_argument("--want", default="TAKE", help="comma list of recommendations to list (TAKE,SKIP,WATCH)")
    ap.add_argument("--step-min", type=int, default=60)
    # cascade timeframes: default is the HTF/swing triad; for INTRADAY frequency use e.g.
    #   --context 4H --setup 15m --confirm 5m --trigger 5m   (or --context 1H --setup 15m ...)
    ap.add_argument("--context", default="4H", choices=["4H", "1H"])
    ap.add_argument("--setup", default="1H", choices=["1H", "15m"])
    ap.add_argument("--confirm", default="15m", choices=["15m", "5m"])
    ap.add_argument("--trigger", default="5m", choices=["5m"])
    ap.add_argument("--money", action="store_true", help="simulate each setup (fill→stop/target) and total the R")
    ap.add_argument("--no-killzone", action="store_true", help="money: drop the killzone filter (more trades)")
    a = ap.parse_args()
    want = tuple(x.strip().upper() for x in a.want.split(","))
    tfs = (a.context, a.setup, a.confirm, a.trigger)
    if a.money:
        res, rnd = simulate(a.symbol, a.start, a.end, tfs=tfs, stage=a.stage,
                            relaxed_retrace=True, min_rr=2.0, killzone=not a.no_killzone)
        kz = "off" if a.no_killzone else "on"
        print(f"# {a.symbol}  MONEY  {a.start}→{a.end}  cascade {'/'.join(tfs)}  (≥2R, killzone {kz}, "
              f"armed=tradeable; target = the draw, stop = manip extreme)")
        print(f"  ICT setups   : {_agg(res)}")
        print(f"  matched-random: {_agg(rnd)}   <- same fills, flipped direction (no-skill control)")
        return
    rows = enumerate_setups(a.symbol, a.start, a.end, stage=a.stage, relaxed=a.relaxed,
                            want=want, step_min=a.step_min, tfs=tfs)
    mode = "RELAXED (killzone/retrace off, RR≥1)" if a.relaxed else "FAITHFUL (killzone/retrace on, RR≥2)"
    casc = "/".join(tfs)
    print(f"# {a.symbol}  {a.stage} candidates  {a.start}→{a.end}  [{mode}]  cascade {casc}  want={','.join(want)}")
    print(f"# {len(rows)} setup(s). 5m data → cascade {casc}. Pull each time (ET) up on a chart.\n")
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
