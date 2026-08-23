"""Replay Dashboard — a browser front-end for the Replay Runner. It does NOT add any trading logic;
it only drives `replay.run.replay` (unchanged) in a background thread and displays its output.

  python -m ict_live.replay.dashboard          # then open the printed http://127.0.0.1:PORT

From the page you can pick one or more symbols, a date range, and an aggregation (overall / monthly /
quarterly), start the replay, watch progress, see the summary statistics (per symbol, side by side),
and download the per-trade CSV. Everything is served from stdlib http.server, so it runs in the same
(pandas) environment as the replay runner — no web framework needed.
"""
from __future__ import annotations

import json
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ict_live.replay import run as REPLAY


class JobManager:
    """Runs replays in background threads and tracks their progress/results. Decoupled from HTTP so
    it can be unit-tested; `replay_fn` is injectable."""

    def __init__(self, replay_fn=None, out_dir=None):
        self.replay_fn = replay_fn or REPLAY.replay
        self.out_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="replay_dash_"))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, symbols, start, end, period="quarter") -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {"id": job_id, "state": "running", "symbols": list(symbols), "from": start, "to": end,
               "period": period, "progress": {}, "results": {}, "csv": {}, "error": None}
        with self._lock:
            self.jobs[job_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job_id

    def _run(self, job: dict) -> None:
        try:
            for sym in job["symbols"]:
                def prog(done, total, _s=sym):
                    job["progress"][_s] = {"done": done, "total": total}
                res = self.replay_fn(sym, job["from"], job["to"], period=job["period"], progress=prog)
                job["results"][sym] = {"overall": res["overall"], "periods": res["periods"],
                                       "bars_5m": res["bars_5m"],
                                       "signals": len(res["runner"].recent_signals)}
                csv_path = self.out_dir / f"{job['id']}_{sym.replace(':', '_').replace('!', '')}.csv"
                n = REPLAY.export_trades_csv(res["runner"], str(csv_path))
                job["csv"][sym] = {"path": str(csv_path), "trades": n}
                job["progress"].setdefault(sym, {})["done"] = job["progress"].get(sym, {}).get("total", 0)
            job["state"] = "done"
        except Exception as e:                                   # surface, don't crash the server
            job["state"], job["error"] = "error", f"{type(e).__name__}: {e}"

    def status(self, job_id: str):
        return self.jobs.get(job_id)

    def csv_path(self, job_id: str, symbol: str):
        j = self.jobs.get(job_id)
        return j["csv"].get(symbol, {}).get("path") if j else None


# --------------------------------------------------------------------------- HTTP front-end

def _page(symbols: list[str]) -> bytes:
    opts = "".join(f'<label class=sym><input type=checkbox value="{s}"> {s}</label>' for s in symbols)
    html = f"""<!doctype html><meta charset=utf-8><title>Replay Dashboard</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:24px;max-width:1100px;color:#111;background:#fff}}
 h1{{font-size:20px}} h2{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#555}}
 .row{{display:flex;gap:18px;flex-wrap:wrap;align-items:flex-end;margin:14px 0}}
 .sym{{margin-right:12px;font-size:14px}} label.fld{{display:flex;flex-direction:column;font-size:12px;color:#555;gap:4px}}
 input,select,button{{font:inherit;padding:6px 8px;border:1px solid #ccc;border-radius:8px;background:#fff;color:#111}}
 button{{background:#0b6;border-color:#0b6;color:#fff;font-weight:600;cursor:pointer;padding:8px 16px}}
 button:disabled{{opacity:.5;cursor:default}}
 table{{border-collapse:collapse;font-size:13px;margin-top:8px}} td,th{{border:1px solid #ddd;padding:4px 8px;text-align:right}}
 th:first-child,td:first-child{{text-align:left}}
 .prog{{height:8px;background:#eee;border-radius:6px;overflow:hidden;width:220px;display:inline-block;vertical-align:middle}}
 .prog>i{{display:block;height:100%;background:#0b6;width:0}}
 .card{{border:1px solid #e3e3e3;border-radius:12px;padding:14px 16px;margin:12px 0}}
 a.dl{{color:#0b6;font-weight:600}} .mut{{color:#777;font-size:12px}}
 @media(prefers-color-scheme:dark){{body{{background:#111;color:#eee}}input,select{{background:#1b1b1b;color:#eee;border-color:#333}}
   td,th{{border-color:#333}}.card{{border-color:#333}}.prog{{background:#333}}h2{{color:#aaa}}}}
</style>
<h1>Replay Dashboard <span class=mut>— front-end for the frozen replay runner</span></h1>
<div class=card>
  <h2>symbols</h2><div class=row id=syms>{opts}</div>
  <div class=row>
    <label class=fld>from<input type=date id=from value="2025-01-01"></label>
    <label class=fld>to<input type=date id=to value="2025-03-31"></label>
    <label class=fld>aggregation<select id=period>
      <option value=quarter>quarterly</option><option value=month>monthly</option>
      <option value=none>overall only</option></select></label>
    <button id=run>Run replay</button>
    <span id=msg class=mut></span>
  </div>
</div>
<div id=out></div>
<script>
const $=s=>document.querySelector(s);
const COLS=[["scored","trades"],["win_rate","win%"],["expectancy_R","exp R"],["profit_factor","PF"],
 ["max_drawdown_R","maxDD"],["total_R","totR"],["longest_win_streak","Wstk"],["longest_loss_streak","Lstk"],
 ["avg_hold_min","avgHold"],["median_hold_min","medHold"]];
function statTable(agg){{
  let h="<tr><th>period</th>"+COLS.map(c=>"<th>"+c[1]+"</th>").join("")+"</tr>";
  let rows="<tr><td>OVERALL</td>"+COLS.map(c=>"<td>"+fmt(agg.overall[c[0]])+"</td>").join("")+"</tr>";
  for(const k of Object.keys(agg.periods||{{}}))
    rows+="<tr><td>"+k+"</td>"+COLS.map(c=>"<td>"+fmt(agg.periods[k][c[0]])+"</td>").join("")+"</tr>";
  return "<table>"+h+rows+"</table>";
}}
function fmt(v){{return (v===null||v===undefined)?"":v;}}
function render(job){{
  let html="";
  for(const sym of job.symbols){{
    const p=job.progress[sym]||{{}}, pct=p.total?Math.round(100*p.done/p.total):0;
    const r=job.results[sym], csv=job.csv[sym];
    html+="<div class=card><h2>"+sym+"</h2>";
    if(!r){{html+="<div class=prog><i style=width:"+pct+"%></i></div> <span class=mut>"+pct+"%</span>";}}
    else{{html+="<div class=mut>"+r.bars_5m+" 5m bars · "+r.signals+" signals · "+(csv?csv.trades:0)+" trades</div>";
      html+=statTable(r);
      if(csv)html+="<p><a class=dl href='/download?job="+job.id+"&symbol="+encodeURIComponent(sym)+"'>⬇ download trades CSV</a></p>";}}
    html+="</div>";
  }}
  if(job.state==="error")html="<div class=card style='border-color:#c33'><b>Error:</b> "+job.error+"</div>"+html;
  $("#out").innerHTML=html;
}}
let timer=null;
async function poll(id){{
  const job=await (await fetch("/status?job="+id)).json();
  render(job);
  if(job.state==="running"){{return;}}
  clearInterval(timer); timer=null; $("#run").disabled=false;
  $("#msg").textContent=job.state==="done"?"done":"stopped";
}}
$("#run").onclick=async()=>{{
  const symbols=[...document.querySelectorAll("#syms input:checked")].map(c=>c.value);
  if(!symbols.length){{$("#msg").textContent="pick at least one symbol";return;}}
  const body={{symbols,from:$("#from").value,to:$("#to").value,period:$("#period").value}};
  $("#run").disabled=true; $("#msg").textContent="starting…"; $("#out").innerHTML="";
  const {{job_id}}=await (await fetch("/run",{{method:"POST",headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify(body)}})).json();
  $("#msg").textContent="running…";
  timer=setInterval(()=>poll(job_id),1000); poll(job_id);
}};
</script>"""
    return html.encode()


def make_handler(jobs: JobManager, symbols: list[str]):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else json.dumps(body, default=str).encode())

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                return self._send(200, _page(symbols), "text/html; charset=utf-8")
            if u.path == "/status":
                job = jobs.status(q.get("job", [""])[0])
                return self._send(200 if job else 404, job or {"error": "unknown job"})
            if u.path == "/download":
                path = jobs.csv_path(q.get("job", [""])[0], q.get("symbol", [""])[0])
                if not path or not Path(path).exists():
                    return self._send(404, {"error": "no csv"})
                data = Path(path).read_bytes()
                return self._send(200, data, "text/csv",
                                  {"Content-Disposition": f'attachment; filename="{Path(path).name}"'})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/run":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            syms = [s for s in body.get("symbols", []) if s in symbols]
            if not syms:
                return self._send(400, {"error": "no valid symbols"})
            job_id = jobs.start(syms, body.get("from"), body.get("to"), body.get("period", "quarter"))
            return self._send(200, {"job_id": job_id})
    return H


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Replay Dashboard (front-end for the replay runner).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ns = ap.parse_args()
    symbols = REPLAY.available_symbols() or ["MES", "MNQ"]
    jobs = JobManager()
    port = ns.port
    for _ in range(20):
        try:
            httpd = ThreadingHTTPServer((ns.host, port), make_handler(jobs, symbols))
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("no free port")
    print(f"Replay Dashboard: http://{ns.host}:{port}  (symbols: {', '.join(symbols)})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
