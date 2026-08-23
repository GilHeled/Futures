"""Unified operational dashboard — the main interface for LIVE monitoring and historical REPLAY.

It adds NO trading logic and NO second implementation of anything. Two areas, both driven by the
existing frozen system:

  LIVE   — server-side proxy of the running live service's existing `/report` endpoint (signals incl.
           SKIP/NO_SETUP, open + closed trades, running stats, engine health). Nothing new server-side.
  REPLAY — the JobManager drives `replay.run.replay` (unchanged) in background threads; endpoints
           /run, /status, /download start a job, report progress, and hand back the per-trade CSV.

Architecture:  browser  ->  dashboard (/live proxy, /run,/status,/download)  ->  existing live/replay
APIs  ->  frozen trading system. Runs under the research venv (pandas, for replay); stdlib HTTP only.

    python -m ict_live.replay.dashboard [--live-url http://127.0.0.1:8000] [--port 8010]
"""
from __future__ import annotations

import json
import tempfile
import threading
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ict_live.replay import run as REPLAY


class JobManager:
    """Runs replays in background threads and tracks progress/results. Decoupled from HTTP so it is
    unit-testable; `replay_fn` is injectable."""

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
            job["state"] = "done"
        except Exception as e:                                   # surface, never crash the server
            job["state"], job["error"] = "error", f"{type(e).__name__}: {e}"

    def status(self, job_id: str):
        return self.jobs.get(job_id)

    def csv_path(self, job_id: str, symbol: str):
        j = self.jobs.get(job_id)
        return j["csv"].get(symbol, {}).get("path") if j else None


def _fetch_live(live_url: str, timeout: float = 2.0) -> dict:
    """Proxy the running live service's existing /report (read-only). Never raises — returns
    {connected: False, ...} when the service is unreachable."""
    url = live_url.rstrip("/") + "/report"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return {"connected": True, "report": json.loads(r.read().decode())}
    except Exception as e:
        return {"connected": False, "error": f"{type(e).__name__}: {e}", "live_url": live_url}


# --------------------------------------------------------------------------- HTTP front-end

_PAGE = r"""<!doctype html><meta charset=utf-8><title>ict_live — operational dashboard</title>
<style>
 :root{color-scheme:light dark}
 body{font-family:system-ui,sans-serif;margin:0;color:#111;background:#fafafa}
 header{background:#0b6;color:#fff;padding:12px 20px;display:flex;gap:20px;align-items:baseline}
 header h1{font-size:16px;margin:0} header a{color:#fff;text-decoration:none;opacity:.85;cursor:pointer}
 header a.on{opacity:1;font-weight:700;text-decoration:underline}
 main{max-width:1200px;margin:0 auto;padding:18px 20px}
 section{display:none} section.on{display:block}
 h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#666;margin:22px 0 6px}
 .card{border:1px solid #e3e3e3;border-radius:12px;padding:12px 14px;margin:10px 0;background:#fff}
 table{border-collapse:collapse;font-size:12.5px;width:100%} td,th{border:1px solid #e0e0e0;padding:4px 8px;text-align:right;white-space:nowrap}
 th:first-child,td:first-child{text-align:left} .scroll{overflow-x:auto}
 .kpi{display:flex;gap:22px;flex-wrap:wrap;font-size:13px} .kpi b{font-size:19px;display:block}
 .row{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end;margin:12px 0}
 label.fld{display:flex;flex-direction:column;font-size:12px;color:#666;gap:4px}
 input,select,button{font:inherit;padding:6px 8px;border:1px solid #ccc;border-radius:8px;background:#fff;color:#111}
 button{background:#0b6;border-color:#0b6;color:#fff;font-weight:600;cursor:pointer;padding:8px 16px}
 button:disabled{opacity:.5;cursor:default} .sym{margin-right:12px;font-size:14px}
 .prog{height:8px;background:#eee;border-radius:6px;overflow:hidden;width:200px;display:inline-block;vertical-align:middle}
 .prog>i{display:block;height:100%;background:#0b6;width:0} a.dl{color:#0b6;font-weight:600}
 .mut{color:#888;font-size:12px} .pill{padding:1px 7px;border-radius:999px;font-size:11px;font-weight:700}
 .TAKE{background:#0b6;color:#fff} .SKIP{background:#e8a100;color:#fff} .NO_SETUP{background:#bbb;color:#fff}
 .pos{color:#0a7} .neg{color:#c33}
 @media(prefers-color-scheme:dark){body{background:#0f0f0f;color:#eee}.card{background:#161616;border-color:#2a2a2a}
  td,th{border-color:#2a2a2a}input,select{background:#1b1b1b;color:#eee;border-color:#333}h2{color:#aaa}.mut{color:#999}}
</style>
<header>
  <h1>ict_live</h1>
  <a id=nav-live class=on onclick="show('live')">LIVE</a>
  <a id=nav-replay onclick="show('replay')">REPLAY</a>
  <span class=mut id=livestate style="margin-left:auto;color:#eaffef"></span>
</header>
<main>
<section id=live class=on>
  <div id=live-body><p class=mut>connecting to live service…</p></div>
</section>

<section id=replay>
  <div class=card>
    <h2>simulation</h2>
    <div class=row id=syms>__OPTS__</div>
    <div class=row>
      <label class=fld>from<input type=date id=from value="2025-01-01"></label>
      <label class=fld>to<input type=date id=to value="2025-03-31"></label>
      <label class=fld>period<select id=period>
        <option value=quarter>quarterly</option><option value=month>monthly</option>
        <option value=none>overall only</option></select></label>
      <button id=run>Run replay</button><span id=msg class=mut></span>
    </div>
  </div>
  <div id=replay-out></div>
</section>
</main>
<script>
const $=s=>document.querySelector(s);
function show(t){for(const s of ['live','replay']){$('#'+s).classList.toggle('on',s===t);$('#nav-'+s).classList.toggle('on',s===t);}}
function fmt(v){return (v===null||v===undefined||v==='')?'':v;}
function num(v){return (v===null||v===undefined)?'':v;}
function cls(v){return (typeof v==='number')?(v>0?'pos':(v<0?'neg':'')):'';}

// ---------- LIVE ----------
function liveTables(rep){
  const s=rep.closed_summary||{}, h=rep.health||{};
  const kpi=`<div class=card><div class=kpi>
    <div>trades<b>${num(s.scored)}</b></div><div>wins<b>${num(s.wins)}</b></div>
    <div>win rate<b>${num(s.win_rate)}</b></div><div>expectancy R<b>${num(s.expectancy_R)}</b></div>
    <div>total R<b class="${cls(s.total_R)}">${num(s.total_R)}</b></div>
    <div>profit factor<b>${num(s.profit_factor)}</b></div><div>max DD R<b>${num(s.max_drawdown_R)}</b></div>
    <div>open<b>${(rep.open_trades||[]).length}</b></div></div></div>`;
  const health=`<div class=card class=mut>engine: signal ${fmt(h.signal_tf)} / entry ${fmt(h.entry_tf)}
    · last bar ${JSON.stringify(h.last_signal_bar||{})} · open ${JSON.stringify(h.open_trades||{})}
    · closed ${JSON.stringify(h.closed_trades||{})}</div>`;
  const openR=(rep.open_trades||[]).map(o=>`<tr><td>${o.symbol}</td><td>${o.direction}</td><td>${o.status}</td>
    <td>${num(o.entry)}</td><td>${num(o.stop)}</td><td>${num(o.exit_target)}</td><td>${num(o.structural_target)}</td>
    <td>${fmt(o.fill_time)}</td></tr>`).join('')||'<tr><td>—</td><td>no open trade</td><td colspan=6></td></tr>';
  const openT=`<h2>open trades</h2><div class=card scroll><table>
    <tr><th>symbol<th>dir<th>status<th>entry<th>stop<th>+2R<th>struct tgt<th>fill</tr>${openR}</table></div>`;
  const closedR=(rep.closed_trades||[]).map(c=>`<tr><td>${fmt(c.close_time)}</td><td>${c.symbol}</td>
    <td>${c.direction}</td><td>${c.result}</td><td class="${cls(c.result_R)}">${num(c.result_R)}</td>
    <td>${num(c.mfe_R)}</td><td>${num(c.mae_R)}</td><td>${num(c.bars_held)}</td></tr>`).join('')
    ||'<tr><td>—</td><td>none yet</td><td colspan=6></td></tr>';
  const closedT=`<h2>closed trades</h2><div class=card scroll><table>
    <tr><th>closed<th>symbol<th>dir<th>result<th>R<th>MFE<th>MAE<th>bars</tr>${closedR}</table></div>`;
  const sigR=(rep.recent_signals||[]).map(s=>{const r=s.reasoning||{};return `<tr>
    <td>${fmt(s.time)}</td><td>${s.symbol}</td><td><span class="pill ${s.action}">${s.action}</span></td>
    <td>${fmt(s.structural)}</td><td>${num(s.entry)}</td><td>${num(s.stop)}</td><td>${num(s.exit_target)}</td>
    <td>${num(s.structural_target)}</td><td>${num(s.confidence)}</td><td>${fmt(s.weakest_factor)}</td>
    <td>${fmt(r.manipulation)}</td><td>${fmt(r.mss)}</td><td>${fmt(r.fvg)}</td><td>${fmt(r.dealing_range)}</td></tr>`;}).join('')
    ||'<tr><td>—</td><td>no signals yet</td><td colspan=12></td></tr>';
  const sigT=`<h2>live signals (incl. SKIP / NO_SETUP)</h2><div class=card scroll><table>
    <tr><th>time<th>sym<th>action<th>dir<th>entry<th>stop<th>+2R<th>struct tgt<th>score<th>weakest
    <th>manipulation<th>MSS<th>FVG<th>dealing range</tr>${sigR}</table></div>`;
  return kpi+openT+closedT+sigT+health;
}
async function pollLive(){
  try{
    const d=await (await fetch('/live')).json();
    if(!d.connected){$('#livestate').textContent='live service not connected';
      $('#live-body').innerHTML=`<div class=card><p>Live service not connected.</p>
      <p class=mut>Start it with <code>python -m ict_live.live.serve</code>, then it appears here.
      Error: ${fmt(d.error)}</p></div>`; return;}
    $('#livestate').textContent='live: connected';
    $('#live-body').innerHTML=liveTables(d.report);
  }catch(e){$('#livestate').textContent='live poll error';}
}
setInterval(pollLive,4000); pollLive();

// ---------- REPLAY ----------
const COLS=[["scored","trades"],["win_rate","win%"],["expectancy_R","exp R"],["profit_factor","PF"],
 ["max_drawdown_R","maxDD"],["total_R","totR"],["longest_win_streak","Wstk"],["longest_loss_streak","Lstk"],
 ["avg_hold_min","avgHold"],["median_hold_min","medHold"]];
function statTable(agg){
  let h="<tr><th>period</th>"+COLS.map(c=>"<th>"+c[1]+"</th>").join("")+"</tr>";
  let rows="<tr><td>OVERALL</td>"+COLS.map(c=>"<td>"+fmt(agg.overall[c[0]])+"</td>").join("")+"</tr>";
  for(const k of Object.keys(agg.periods||{}))
    rows+="<tr><td>"+k+"</td>"+COLS.map(c=>"<td>"+fmt(agg.periods[k][c[0]])+"</td>").join("")+"</tr>";
  return "<div class=card scroll><table>"+h+rows+"</table></div>";
}
function renderReplay(job){
  let html="";
  if(job.state==="error")html+="<div class=card style='border-color:#c33'><b>Error:</b> "+fmt(job.error)+"</div>";
  for(const sym of job.symbols){
    const p=job.progress[sym]||{}, pct=p.total?Math.round(100*p.done/p.total):0;
    const r=job.results[sym], csv=job.csv[sym];
    html+="<div class=card><h2>"+sym+"</h2>";
    if(!r)html+="<div class=prog><i style=width:"+pct+"%></i></div> <span class=mut>"+pct+"%</span>";
    else{html+="<div class=mut>"+r.bars_5m+" 5m bars · "+r.signals+" signals · "+(csv?csv.trades:0)+" trades</div>"
      +statTable(r);
      if(csv)html+="<p><a class=dl href='/download?job="+job.id+"&symbol="+encodeURIComponent(sym)+"'>⬇ download trades CSV</a></p>";}
    html+="</div>";
  }
  $("#replay-out").innerHTML=html;
}
let timer=null;
async function pollReplay(id){
  const job=await (await fetch("/status?job="+id)).json();
  renderReplay(job);
  if(job.state==="running")return;
  clearInterval(timer);timer=null;$("#run").disabled=false;$("#msg").textContent=job.state;
}
$("#run").onclick=async()=>{
  const symbols=[...document.querySelectorAll("#syms input:checked")].map(c=>c.value);
  if(!symbols.length){$("#msg").textContent="pick at least one symbol";return;}
  const body={symbols,from:$("#from").value,to:$("#to").value,period:$("#period").value};
  $("#run").disabled=true;$("#msg").textContent="starting…";$("#replay-out").innerHTML="";
  const {job_id}=await (await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)})).json();
  $("#msg").textContent="running…";timer=setInterval(()=>pollReplay(job_id),1000);pollReplay(job_id);
};
</script>"""


def _page(symbols: list[str]) -> bytes:
    opts = "".join(f'<label class=sym><input type=checkbox value="{s}"> {s}</label>' for s in symbols)
    return _PAGE.replace("__OPTS__", opts).encode()


def make_handler(jobs: JobManager, symbols: list[str], live_fetch=None):
    live_fetch = live_fetch or (lambda: {"connected": False, "error": "no live url configured"})

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
            if u.path == "/live":
                return self._send(200, live_fetch())
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
            if urlparse(self.path).path != "/run":
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
    ap = argparse.ArgumentParser(description="ict_live operational dashboard (live + replay).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--live-url", default="http://127.0.0.1:8000",
                    help="base URL of the running live service (for the LIVE area)")
    ns = ap.parse_args()
    symbols = REPLAY.available_symbols() or ["MES", "MNQ"]
    jobs = JobManager()
    handler = make_handler(jobs, symbols, live_fetch=lambda: _fetch_live(ns.live_url))
    port = ns.port
    for _ in range(20):
        try:
            httpd = ThreadingHTTPServer((ns.host, port), handler)
            break
        except OSError:
            port += 1
    else:
        raise SystemExit("no free port")
    print(f"ict_live dashboard: http://{ns.host}:{port}   (LIVE proxies {ns.live_url}; "
          f"replay symbols: {', '.join(symbols)})")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
