"""Monitoring report — a SIMPLE status view (JSON + one plain HTML page), no dashboard. Shows the
current open trade, last 20 signals, tracker state, closed trades, win rate / expectancy / average R,
and engine health (last processed bar). Read-only; computed from the runner's in-memory state (which
is reconstructable from the append-only store on restart)."""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable

RECENT = 20


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def aggregate_closed(closed: Iterable[dict]) -> dict:
    # chronological (by close time) so streaks / drawdown / equity are ordered correctly
    rows = sorted(closed, key=lambda r: r.get("close_time") or "")
    filled = [r for r in rows if r.get("filled")]
    scored = [r for r in filled if r.get("result_R") is not None]
    rs = [r["result_R"] for r in scored]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)

    cum = peak = maxdd = 0.0                       # equity curve in R, peak-to-trough drawdown
    for x in rs:
        cum += x
        peak = max(peak, cum)
        maxdd = max(maxdd, peak - cum)

    lw = ll = cw = cl = 0                          # longest winning / losing streaks
    for x in rs:
        cw, cl = (cw + 1, 0) if x > 0 else ((0, cl + 1) if x < 0 else (0, 0))
        lw, ll = max(lw, cw), max(ll, cl)

    holds = []                                     # hold time in minutes, fill -> close
    for r in scored:
        ft, ct = r.get("fill_time"), r.get("close_time")
        if ft and ct:
            try:
                holds.append((datetime.fromisoformat(ct) - datetime.fromisoformat(ft)).total_seconds() / 60)
            except ValueError:
                pass
    return {"n": len(rows), "filled": len(filled), "scored": len(scored),
            "no_fill": sum(1 for r in rows if r.get("filled") is False),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(scored), 3) if scored else None,
            "expectancy_R": round(sum(rs) / len(rs), 3) if rs else None,
            "avg_R": round(sum(rs) / len(rs), 3) if rs else None,
            "total_R": round(sum(rs), 2) if rs else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "max_drawdown_R": round(maxdd, 2) if rs else None,
            "longest_win_streak": lw, "longest_loss_streak": ll,
            "avg_hold_min": round(sum(holds) / len(holds), 1) if holds else None,
            "median_hold_min": round(_median(holds), 1) if holds else None}


def build_report(runner) -> dict:
    open_trades, closed = [], []
    for sym, tr in runner.trackers.items():
        for o in tr.open.values():
            open_trades.append({"symbol": sym, **{k: getattr(o, k) for k in
                               ("ticket_id", "direction", "entry", "stop", "exit_target",
                                "structural_target", "status", "fill_time", "bars_since_fill",
                                "opened_time")},
                                "reasoning": o.reasoning,
                                "execution_score": o.reasoning.get("execution_score"),
                                "weakest_factor": o.reasoning.get("weakest_factor")})
        closed += [c.to_dict() for c in tr.closed]
    closed_sorted = sorted(closed, key=lambda c: c.get("close_time") or "")
    return {
        "health": runner.health(),
        "open_trades": open_trades,
        "current": dict(getattr(runner, "last_signal", {})),      # latest ticket per symbol
        "recent_signals": runner.recent_signals[-RECENT:][::-1],
        "closed_summary": aggregate_closed(closed),
        "closed_trades": closed_sorted[-RECENT:][::-1],
    }


def _row(cells):
    return "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in cells) + "</tr>"


def render_html(rep: dict) -> str:
    h = rep["health"]
    s = rep["closed_summary"]
    sig_rows = "".join(_row([t.get("time", ""), t.get("symbol", ""), t.get("action", ""),
                             t.get("structural", ""), t.get("execution", ""),
                             t.get("entry", ""), t.get("stop", ""), t.get("exit_target", "")])
                       for t in rep["recent_signals"])
    open_rows = "".join(_row([o["symbol"], o["direction"], o["status"], o["entry"], o["stop"],
                              o["exit_target"], o.get("fill_time", "")]) for o in rep["open_trades"]) \
        or _row(["—", "no open trade", "", "", "", "", ""])
    closed_rows = "".join(_row([c.get("close_time", ""), c["symbol"], c["direction"], c["result"],
                                c.get("result_R", ""), c.get("win", ""), c.get("bars_held", "")])
                          for c in rep["closed_trades"]) or _row(["—", "none yet", "", "", "", "", ""])
    css = ("body{font-family:system-ui,sans-serif;margin:24px;color:#111;background:#fff}"
           "h1{font-size:18px}h2{font-size:14px;margin-top:22px;text-transform:uppercase;"
           "letter-spacing:.08em;color:#555}table{border-collapse:collapse;width:100%;font-size:13px}"
           "td,th{border:1px solid #ddd;padding:4px 8px;text-align:left}"
           ".kpi{display:flex;gap:24px;flex-wrap:wrap;font-size:14px}.kpi b{font-size:20px;display:block}"
           "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}td,th{border-color:#333}"
           "h2{color:#aaa}}")
    return (f"<!doctype html><meta charset=utf-8><title>ict_live monitor</title><style>{css}</style>"
            f"<h1>ict_live — monitor</h1>"
            f"<div class=kpi>"
            f"<div>win rate<b>{s['win_rate']}</b></div>"
            f"<div>expectancy R<b>{s['expectancy_R']}</b></div>"
            f"<div>profit factor<b>{s['profit_factor']}</b></div>"
            f"<div>max DD R<b>{s['max_drawdown_R']}</b></div>"
            f"<div>closed<b>{s['scored']}</b></div>"
            f"<div>total R<b>{s['total_R']}</b></div>"
            f"<div>open<b>{len(rep['open_trades'])}</b></div></div>"
            f"<h2>engine health</h2><table>{_row(['signal_tf', h['signal_tf']])}"
            f"{_row(['entry_tf', h['entry_tf']])}{_row(['last signal bar', h['last_signal_bar']])}"
            f"{_row(['open trades', h['open_trades']])}{_row(['closed trades', h['closed_trades']])}</table>"
            f"<h2>open trade</h2><table><tr><th>symbol<th>dir<th>status<th>entry<th>stop<th>+2R exit<th>fill</tr>{open_rows}</table>"
            f"<h2>last {RECENT} signals</h2><table>"
            f"<tr><th>time<th>symbol<th>action<th>structural<th>exec<th>entry<th>stop<th>+2R exit</tr>{sig_rows}</table>"
            f"<h2>closed trades</h2><table>"
            f"<tr><th>closed<th>symbol<th>dir<th>result<th>R<th>win<th>bars</tr>{closed_rows}</table>")
