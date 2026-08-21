"""Phase-0 capability probe for the TradingView MCP (DEV-ONLY).

Answers ONLY the four approved unknowns and classifies each capability SAFE / CONDITIONAL /
UNSAFE for CAUSAL fidelity work. Builds NO overlays and NO visual_audit. Read-mostly: it
creates at most a couple of throwaway drawings for probe 1 and clears them.

    1. draw_shape anchoring — can rectangles/lines/labels anchor by (time, price), not just price?
    2. ohlcv timestamp format / timezone / ordering.
    3. does ohlcv respect the Bar Replay cursor, or leak bars beyond it?
    4. do screenshots / Pine objects / OHLCV during replay expose ONLY data at/left of the
       cursor? Any tool that leaks future state is marked UNSAFE for causal audits.

Run locally where TradingView Desktop is up with the debug port and the `tv` CLI is installed:

    TV_CLI=tv python3 -m ict_live.devtools.tvmcp.probe --symbol ES1! --timeframe 60 \
        --replay-date 2025-03-14

Writes a JSON + Markdown report under ict_live/devtools/tvmcp/results/. If the MCP is
unreachable it writes a "not executed" report with remediation and exits 2 — it never fabricates
observations.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ict_live.devtools.tvmcp.client import TvClient, TvResult

RESULTS = Path(__file__).with_name("results")
SAFE, CONDITIONAL, UNSAFE, UNKNOWN = "SAFE", "CONDITIONAL", "UNSAFE", "UNKNOWN"

# a read tool leaks if it exposes any datum newer than the replay cursor by more than this many
# seconds (one bar of slack for boundary/labeling differences; tightened per-TF by the caller)
_SLACK_S = 90.0


# ---------- timestamp helpers (schema-agnostic: we don't know the exact JSON shape) ----------
def to_epoch(v: Any) -> Optional[float]:
    """Best-effort convert a value to epoch seconds; None if it isn't a timestamp."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        if f >= 1e17: return f / 1e9      # ns
        if f >= 1e14: return f / 1e6      # µs
        if f >= 1e11: return f / 1e3      # ms
        if f >= 1e9:  return f            # s
        return None
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(int(s)) if s.lstrip("-").isdigit() and abs(int(s)) >= 1e9 else None
        except ValueError:
            pass
        try:
            iso = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def collect_timestamps(obj: Any, key_hint=("time", "timestamp", "t", "date", "datetime", "ts")) \
        -> list[tuple[str, float]]:
    """Recursively gather (path, epoch_s) for every value that parses as a timestamp. Values
    under a time-ish key are trusted; bare large numbers elsewhere are also captured (and shown
    so a human can sanity-check what got treated as a timestamp)."""
    found: list[tuple[str, float]] = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, val in o.items():
                walk(val, f"{path}.{k}")
        elif isinstance(o, list):
            for i, val in enumerate(o[:200]):
                walk(val, f"{path}[{i}]")
        else:
            e = to_epoch(o)
            if e is not None:
                found.append((path, e))
    walk(obj, "$")
    return found


def latest_ts(obj: Any) -> Optional[float]:
    ts = [e for _, e in collect_timestamps(obj)]
    return max(ts) if ts else None


def iso(e: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(e, tz=timezone.utc).isoformat() if e else None


# ---------- verdict ----------
def leak_verdict(latest: Optional[float], cursor: Optional[float], *,
                 filterable: bool) -> tuple[str, str]:
    if cursor is None:
        return UNKNOWN, "no reliable replay cursor timestamp to compare against"
    if latest is None:
        return UNKNOWN, "tool returned no parseable timestamps"
    delta = latest - cursor
    if delta <= _SLACK_S:
        return SAFE, f"latest datum {iso(latest)} <= cursor {iso(cursor)} (+{delta:.0f}s)"
    if filterable:
        return CONDITIONAL, (f"LEAK: latest {iso(latest)} is {delta:.0f}s beyond cursor "
                             f"{iso(cursor)}; usable ONLY with deterministic client-side "
                             f"truncation to ts<=cursor")
    return UNSAFE, f"LEAK: latest {iso(latest)} is {delta:.0f}s beyond cursor {iso(cursor)}"


# ---------- probes ----------
def probe_cli_surface(tv: TvClient) -> dict:
    cmds = [("--help",), ("ohlcv", "--help"), ("draw", "--help"), ("replay", "--help"),
            ("screenshot", "--help"), ("data", "--help")]
    out = {}
    for c in cmds:
        r = tv.run(*c, timeout=15)
        out[" ".join(c)] = {"ok": r.ok, "stdout": r.stdout[:4000], "stderr": r.stderr[:1000],
                            "error": r.error}
    return out


def _anchors(tv: TvClient) -> tuple[Optional[int], Optional[int], float, float]:
    """Realistic (time1,time2,price1,price2) from recent bars so probe drawings land on-chart."""
    r = tv.ohlcv(summary=True)
    bars = (r.data or {}).get("last_5_bars") if isinstance(r.data, dict) else None
    if bars and len(bars) >= 2:
        t1, t2 = int(bars[0]["time"]), int(bars[-1]["time"])
        c = float(bars[-1]["close"])
        return t1, t2, c, c * 1.001
    now = int(time.time())
    return now - 3600, now - 1800, 100.0, 101.0


def probe_draw_anchoring(tv: TvClient) -> dict:
    """Try to create a time+price-anchored rectangle and a text label, then read them back.
    Exact param syntax is undocumented, so we try candidate forms and record which (if any)
    the installed CLI accepts and round-trips with two distinct timestamps."""
    help_txt = tv.help("draw").stdout
    t1, t2, p1, p2 = _anchors(tv)
    attempts = [
        ("rect time+price", ("draw", "shape", "-t", "rectangle",
                             "-p", p1, "--time", t1, "--price2", p2, "--time2", t2)),
        ("text time+price", ("draw", "shape", "-t", "text", "--time", t1, "-p", p1,
                             "--text", "probe")),
        ("hline price only", ("draw", "shape", "-t", "horizontal_line", "-p", p1)),
    ]
    results = []
    for name, args in attempts:
        r = tv.run(*args, timeout=15)
        results.append({"attempt": name, "args": [str(a) for a in args],
                        "ok": r.ok, "data": r.data, "stdout": r.stdout[:800],
                        "stderr": r.stderr[:400], "error": r.error})
    listed = tv.draw_list()
    ts_in_list = collect_timestamps(listed.data) if listed.data is not None else []
    tv.draw_clear()   # cleanup throwaway drawings
    # verdict: a time-anchored attempt (rect/text) accepted AND >=2 distinct timestamps echoed back
    time_attempts = [a for a in results if a["attempt"] in ("rect time+price", "text time+price")]
    any_time_attempt_ok = any(a["ok"] for a in time_attempts)
    has_two_ts = len({round(e) for _, e in ts_in_list}) >= 2
    if any_time_attempt_ok and has_two_ts:
        verdict, note = "TIME+PRICE", "a time-anchored shape was accepted and round-tripped >=2 timestamps"
    elif any(a["ok"] for a in time_attempts):
        verdict, note = "TIME+PRICE?", "time-anchored draw accepted but draw list did not echo timestamps to confirm; verify via screenshot"
    elif any(a["ok"] for a in results if a["attempt"] == "hline price only"):
        verdict, note = "PRICE_ONLY", "only price-anchored shapes confirmed; time anchoring not established"
    else:
        verdict, note = UNKNOWN, "no draw attempt succeeded; confirm CLI param syntax from help output"
    return {"help": help_txt[:4000], "attempts": results,
            "draw_list_after": listed.data, "timestamps_in_list": ts_in_list[:20],
            "verdict": verdict, "note": note}


def probe_ohlcv_format(tv: TvClient) -> dict:
    r = tv.run("ohlcv", timeout=30)
    ts = collect_timestamps(r.data) if r.data is not None else []
    time_paths = [(p, e) for p, e in ts if any(h in p.lower()
                  for h in ("time", "date", "ts", "\"t\"", ".t"))]
    ordering = UNKNOWN
    sample_paths = [p for p, _ in ts[:6]]
    if len(ts) >= 2:
        seq = [e for _, e in ts]
        ordering = "ascending" if seq[0] <= seq[-1] else "descending"
    fmt = UNKNOWN
    # infer format from a raw numeric sample if present
    raw_first = None
    if isinstance(r.data, (dict, list)):
        raw_first = json.dumps(r.data)[:500]
    return {"ok": r.ok, "error": r.error, "raw_head": raw_first,
            "n_timestamps": len(ts), "sample_paths": sample_paths,
            "time_field_candidates": time_paths[:5],
            "earliest": iso(min((e for _, e in ts), default=None) if ts else None),
            "latest": iso(max((e for _, e in ts), default=None) if ts else None),
            "ordering": ordering,
            "note": ("Timezone CANNOT be inferred from the feed alone — cross-check the latest "
                     "bar's time against our ET raw store during a live capture before trusting.")}


def _cursor(status_res: TvResult, last_action_res: Optional[TvResult]) -> Optional[float]:
    """Replay cursor time: prefer status, else the current_date from the last start/step."""
    c = latest_ts(status_res.data) if status_res.data is not None else None
    if c is None and last_action_res is not None and last_action_res.data is not None:
        cd = (last_action_res.data or {}).get("current_date") if isinstance(last_action_res.data, dict) else None
        c = to_epoch(cd) if cd is not None else latest_ts(last_action_res.data)
    return c


def probe_replay_cursor(tv: TvClient, replay_date: str, steps: int = 3) -> dict:
    start = tv.replay_start(replay_date)
    obs = []
    verdict_overall = SAFE
    last_action = start
    for i in range(steps + 1):
        st = tv.replay_status()
        cursor = _cursor(st, last_action)
        oh = tv.ohlcv()
        latest = latest_ts(oh.data)
        v, note = leak_verdict(latest, cursor, filterable=True)
        obs.append({"step": i, "cursor": iso(cursor), "ohlcv_latest": iso(latest),
                    "verdict": v, "note": note,
                    "status_raw": (json.dumps(st.data)[:300] if st.data is not None else st.stdout[:300])})
        if v == UNSAFE:
            verdict_overall = UNSAFE
        elif v == CONDITIONAL and verdict_overall != UNSAFE:
            verdict_overall = CONDITIONAL
        elif v == UNKNOWN and verdict_overall == SAFE:
            verdict_overall = UNKNOWN
        if i < steps:
            last_action = tv.replay_step()
    tv.replay_stop()
    return {"replay_start_ok": start.ok, "start_raw": (json.dumps(start.data)[:300]
            if start.data is not None else start.stderr[:300]),
            "observations": obs, "verdict": verdict_overall}


def probe_general_leak(tv: TvClient, replay_date: str) -> dict:
    start = tv.replay_start(replay_date)
    st = tv.replay_status()
    cursor = _cursor(st, start)
    tools = {
        "ohlcv": (tv.ohlcv(), True),
        "quote": (tv.quote(), False),
        "study_values": (tv.study_values(), False),
        "pine_lines": (tv.pine_lines(), True),
        "pine_labels": (tv.pine_labels(), True),
        "pine_boxes": (tv.pine_boxes(), True),
    }
    per_tool = {}
    for name, (res, filterable) in tools.items():
        latest = latest_ts(res.data) if res.data is not None else None
        v, note = leak_verdict(latest, cursor, filterable=filterable)
        # tools that returned no timestamps at all can't be proven safe -> conservative UNKNOWN
        per_tool[name] = {"ok": res.ok, "error": res.error, "latest": iso(latest),
                          "verdict": v, "note": note}
    # screenshot can't be parsed for timestamps -> always needs visual review
    ss = tv.screenshot("chart")
    per_tool["screenshot"] = {"ok": ss.ok, "error": ss.error, "verdict": CONDITIONAL,
                              "note": ("pixels can't be timestamp-checked; SAFE only if replay "
                                       "hides bars right of the cursor AND the report crops to "
                                       "the cursor — verify visually once.")}
    tv.replay_stop()
    return {"cursor": iso(cursor), "per_tool": per_tool}


def _report(payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (RESULTS / f"phase0_probe_{stamp}.json").write_text(json.dumps(payload, indent=2, default=str))
    md = _render_md(payload)
    p = RESULTS / f"phase0_probe_{stamp}.md"
    p.write_text(md)
    return p


def _render_md(p: dict) -> str:
    L = ["# TradingView MCP — Phase-0 Probe Report", ""]
    L += [f"- generated: {p.get('generated')}", f"- symbol/tf: {p.get('symbol')} / {p.get('timeframe')}",
          f"- replay date: {p.get('replay_date')}", f"- tv reachable: {p.get('reachable')}", ""]
    if not p.get("reachable"):
        L += ["## NOT EXECUTED", p.get("remediation", ""), ""]
        return "\n".join(L)
    d1 = p["draw_anchoring"]; d3 = p["replay_cursor"]; d4 = p["general_leak"]
    L += ["## Verdict summary", "",
          f"1. draw_shape anchoring: **{d1['verdict']}** — {d1['note']}",
          f"2. ohlcv format: ordering **{p['ohlcv_format']['ordering']}**, "
          f"latest {p['ohlcv_format']['latest']}",
          f"3. ohlcv respects replay cursor: **{d3['verdict']}**",
          "4. per-tool future-leak during replay:"]
    for name, v in d4["per_tool"].items():
        L.append(f"   - `{name}`: **{v['verdict']}** — {v['note']}")
    L += ["", "## Recommended fallbacks", "",
          "- If (1) is PRICE_ONLY/UNKNOWN: draw price levels as horizontal_lines; render "
          "time-located objects (FVG boxes, displacement legs, sweep/AMD labels) into a "
          "side-by-side Python panel instead of onto the chart.",
          "- If (3) is CONDITIONAL: apply deterministic client-side truncation to ts<=cursor "
          "before any comparison; if UNSAFE: do not read ohlcv during replay — feed the engine "
          "our own raw 1m and use the chart for the picture only.",
          "- Any tool marked UNSAFE/UNKNOWN must not be used in a causal audit until re-probed.",
          "", "Full evidence is in the sibling .json file."]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="ES1!")
    ap.add_argument("--timeframe", default="60")
    ap.add_argument("--replay-date", default=None,
                    help="ISO date for probes 3 & 4 (a past trading day). If omitted they are skipped.")
    ap.add_argument("--tv-cli", default=None)
    ap.add_argument("--tv-cwd", default=None)
    a = ap.parse_args(argv)

    tv = TvClient(binary=a.tv_cli, cwd=a.tv_cwd)
    payload: dict = {"generated": datetime.now().isoformat(), "symbol": a.symbol,
                     "timeframe": a.timeframe, "replay_date": a.replay_date}

    health = tv.health()
    connected = bool(isinstance(health.data, dict) and health.data.get("cdp_connected"))
    if not (health.ok and connected):
        payload["reachable"] = False
        payload["remediation"] = (
            "TradingView MCP not reachable. Ensure: TradingView Desktop is running, launched with "
            "--remote-debugging-port=9222 (or run `tv launch`); the `tv` CLI is installed "
            "(`npm link` in the cloned repo, or set TV_CLI='node /path/to/repo/src/cli/index.js' "
            "and --tv-cwd); then `tv health_check` succeeds. Re-run this probe.")
        payload["health_raw"] = {"stdout": health.stdout[:500], "stderr": health.stderr[:500],
                                 "error": health.error}
        path = _report(payload)
        print(f"[probe] MCP unreachable — wrote {path}")
        return 2

    payload["reachable"] = True
    tv.set_symbol(a.symbol)
    tv.set_timeframe(a.timeframe)
    payload["cli_surface"] = probe_cli_surface(tv)
    payload["draw_anchoring"] = probe_draw_anchoring(tv)
    payload["ohlcv_format"] = probe_ohlcv_format(tv)
    if a.replay_date:
        payload["replay_cursor"] = probe_replay_cursor(tv, a.replay_date)
        payload["general_leak"] = probe_general_leak(tv, a.replay_date)
    else:
        skip = {"verdict": UNKNOWN, "note": "skipped: no --replay-date", "per_tool": {}, "observations": []}
        payload["replay_cursor"] = skip
        payload["general_leak"] = skip
    path = _report(payload)
    print(f"[probe] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
