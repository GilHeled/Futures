"""Replay Runner — `python -m ict_live.replay.run --symbol MES --from 2025-01-01 --to 2025-03-31`.

Feeds historical bars through the SAME production pipeline the live webhook service uses. The only
difference from live is the data source: instead of TradingView pushing 1m webhooks, we read cached
historical bars and expand each into aggregation-preserving 1-minute bars, then feed them to the
identical Ingestor → BarBuilder → LiveRunner → frozen engine/filter/exit → TradeTracker → journal.
At the end it prints/writes the same report the live monitor would show.

Historical bars are 5m (Databento); each 5m is split into 5 one-minute bars whose OHLCV aggregates
back exactly to the 5m, so every resampled timeframe (15m/1H) — and therefore every TradeTicket and
trade — is identical to what the pipeline would produce from a true 1m feed. Resolution happens on
signal-TF bars in both live and replay, so intrabar 1m detail is irrelevant to the outcomes.

Runs under the research venv (`.venv/bin/python`, needs pandas to load history); imports the pandas
loader lazily so `to_1m_payloads` stays importable in the FastAPI env used by the acceptance test.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ict_live.feeds.ingestor import Ingestor
from ict_live.live import report as REPORT
from ict_live.live.runner import LiveRunner
from ict_live.storage.market_store import MarketStore

SCHEMA = "ict_live.bar.v1"


def _payload(symbol, ot, ct, o, h, l, c, v) -> dict:
    return {"schema": SCHEMA, "symbol": symbol, "resolution": "1",
            "bar_time_ms": int(ot.timestamp() * 1000), "bar_close_ms": int(ct.timestamp() * 1000),
            "open": o, "high": h, "low": l, "close": c, "volume": v}


def to_1m_payloads(bar, symbol: str) -> list[dict]:
    """Split any bar into 1-minute webhook payloads that aggregate back to it EXACTLY (open, max
    high, min low, last close, summed volume). A four-phase path (open → high → low → close) keeps
    the sub-bars plausible; the aggregate is what matters for resampling."""
    n = max(1, round((bar.close_time - bar.open_time).total_seconds() / 60))
    o, h, l, c, v = bar.open, bar.high, bar.low, bar.close, bar.volume / n
    out = []

    def add(k, oo, hh, ll, cc):
        ot = bar.open_time + timedelta(minutes=k)
        out.append(_payload(symbol, ot, ot + timedelta(minutes=1), oo, hh, ll, cc, v))
    if n >= 4:
        add(0, o, o, o, o)
        add(1, o, h, o, h)                       # print the high
        add(2, h, h, l, l)                       # print the low
        for k in range(3, n - 1):
            add(k, l, l, l, l)
        add(n - 1, l, max(l, c), min(l, c), c)   # settle at the close
    else:
        add(0, o, h, l, c)
        for k in range(1, n):
            add(k, c, c, c, c)
    return out


def build_runner(*, signal_tf: str = "1H", entry_tf: str = "15m",
                 data_dir: Optional[str] = None, horizon: Optional[int] = None) -> LiveRunner:
    store = MarketStore(path=(Path(data_dir) / "raw_1m.jsonl")) if data_dir else MarketStore()
    ing = Ingestor(store=store)                  # no token in replay
    kw = {"signal_tf": signal_tf, "entry_tf": entry_tf,
          "store_dir": (str(Path(data_dir) / "signals") if data_dir else None)}
    if horizon is not None:
        kw["horizon"] = horizon
    return LiveRunner(ing, **kw)


def feed_bars(runner: LiveRunner, bars, symbol: str) -> int:
    """Feed a chronological list of bars through the live pipeline as 1m webhook payloads."""
    n = 0
    for b in bars:
        for p in to_1m_payloads(b, symbol):
            runner.feed(p)
            n += 1
    return n


def tv_symbol(data_symbol: str) -> str:
    """Map a historical data key (e.g. MES) to the TradingView instrument id the Ingestor knows
    (CME_MINI:MES1!), so replay routes through the same symbol validation as the live feed."""
    from ict_live import config as C
    for tv, inst in C.INSTRUMENTS.items():
        if inst.root == data_symbol or tv == data_symbol:
            return tv
    return data_symbol


def all_closed(runner) -> list[dict]:
    """Every closed trade across the runner's trackers (build_report only keeps the last N)."""
    return [c.to_dict() for tr in runner.trackers.values() for c in tr.closed]


def _period_key(iso: str, mode: str) -> str:
    dt = datetime.fromisoformat(iso)
    if mode == "month":
        return f"{dt.year}-{dt.month:02d}"
    return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"          # quarter (attributed by the trade's start)


def by_period(closed: list[dict], mode: str) -> dict:
    """Group closed trades by the period they were INITIATED in (opened_time) and aggregate each."""
    groups: dict[str, list] = {}
    for r in closed:
        key = _period_key(r.get("opened_time") or r.get("close_time"), mode)
        groups.setdefault(key, []).append(r)
    return {k: REPORT.aggregate_closed(groups[k]) for k in sorted(groups)}


def replay(symbol: str, start: str, end: str, *, signal_tf: str = "1H", entry_tf: str = "15m",
           data_dir: Optional[str] = None, period: str = "none") -> dict:
    from ict_live.research import data as data_mod          # lazy: pandas only needed here
    bars5 = data_mod.load_5m(symbol, start=start, end=end)
    runner = build_runner(signal_tf=signal_tf, entry_tf=entry_tf, data_dir=data_dir)
    feed_sym = tv_symbol(symbol)
    fed = feed_bars(runner, bars5, feed_sym)
    closed = all_closed(runner)
    return {"symbol": symbol, "feed_symbol": feed_sym, "from": start, "to": end,
            "bars_5m": len(bars5), "bars_1m_fed": fed, "runner": runner,
            "report": REPORT.build_report(runner),
            "overall": REPORT.aggregate_closed(closed),
            "periods": (by_period(closed, period) if period and period != "none" else {})}


_COLS = [("scored", "trades"), ("win_rate", "win%"), ("expectancy_R", "exp R"),
         ("profit_factor", "PF"), ("max_drawdown_R", "maxDD"), ("total_R", "totR"),
         ("longest_win_streak", "Wstk"), ("longest_loss_streak", "Lstk"),
         ("avg_hold_min", "avgHold"), ("median_hold_min", "medHold")]


def render_period_table(overall: dict, periods: dict) -> str:
    head = "period      | " + " | ".join(f"{lbl:>7}" for _, lbl in _COLS)
    line = "-" * len(head)

    def row(label, a):
        return f"{label:<11} | " + " | ".join(f"{('' if a.get(k) is None else a[k]):>7}" for k, _ in _COLS)
    out = [head, line, row("OVERALL", overall)]
    if periods:
        out.append(line)
        out += [row(k, v) for k, v in periods.items()]
    return "\n".join(out)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Replay historical bars through the live pipeline.")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--signal-tf", default="1H")
    ap.add_argument("--entry-tf", default="15m")
    ap.add_argument("--period", choices=("none", "month", "quarter"), default="quarter",
                    help="break the results down by period (default: quarter)")
    ap.add_argument("--data-dir", default=None, help="persist raw 1m + signal/trade logs here")
    ap.add_argument("--out", default=None, help="write the report as HTML (….html) or JSON")
    ns = ap.parse_args()
    res = replay(ns.symbol, ns.start, ns.end, signal_tf=ns.signal_tf, entry_tf=ns.entry_tf,
                 data_dir=ns.data_dir, period=ns.period)
    if res["bars_5m"] == 0:
        print(f"WARNING: no cached data for {ns.symbol} in {ns.start}..{ns.end} — nothing to replay. "
              f"The replay reads the local historical cache (not TradingView); check the available "
              f"date range.")
        return
    if ns.out:
        p = Path(ns.out)
        p.write_text(REPORT.render_html(res["report"]) if p.suffix == ".html"
                     else json.dumps({"overall": res["overall"], "periods": res["periods"]},
                                     indent=1, default=str))
    print(f"Replay {ns.symbol} {ns.start} -> {ns.end}  "
          f"({res['bars_5m']} 5m bars, {len(res['runner'].recent_signals)} signals seen)\n")
    print(render_period_table(res["overall"], res["periods"]))


if __name__ == "__main__":
    main()
