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

_PAGE = r"""<!doctype html><meta charset=utf-8><title>ict_live — trading dashboard</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>
 :root{
   --bg:#f4f5f7; --panel:#ffffff; --panel2:#fafbfc; --line:#e4e7ec; --ink:#111827; --mut:#6b7280;
   --chrome:#0f172a; --chrome-ink:#e5e7eb; --accent:#4f46e5;
   --long:#059669; --long-bg:#ecfdf5; --short:#dc2626; --short-bg:#fef2f2;
   --warn:#b45309; --warn-bg:#fffbeb; --info:#2563eb; --info-bg:#eff6ff;
   color-scheme:light dark;
 }
 @media(prefers-color-scheme:dark){:root{
   --bg:#0b0e14; --panel:#131722; --panel2:#0f131b; --line:#242a37; --ink:#e6e9ef; --mut:#8b93a7;
   --chrome:#0a0d13; --chrome-ink:#e6e9ef; --accent:#8b8cf7;
   --long:#22c55e; --long-bg:#0c2018; --short:#f04747; --short-bg:#241014;
   --warn:#eab308; --warn-bg:#231d09; --info:#60a5fa; --info-bg:#0e1a2e;
 }}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
   font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,system-ui,sans-serif;font-size:14px}
 .num{font-variant-numeric:tabular-nums;font-family:"SF Mono",ui-monospace,Menlo,monospace}
 header{position:sticky;top:0;z-index:5;background:var(--chrome);color:var(--chrome-ink);
   display:flex;align-items:center;gap:14px;padding:10px 20px;border-bottom:1px solid #0006}
 header .brand{font-weight:800;letter-spacing:.02em} header .sub{color:#9aa3b2;font-size:12px}
 nav{display:flex;gap:6px;margin-left:14px}
 nav a{color:#c7cdd9;cursor:pointer;padding:6px 14px;border-radius:999px;font-weight:600;font-size:13px}
 nav a.on{background:var(--accent);color:#fff}
 .conn{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:12px;color:#9aa3b2}
 .dot{width:9px;height:9px;border-radius:50%;background:#6b7280} .dot.ok{background:#22c55e} .dot.no{background:#ef4444}
 main{max-width:1180px;margin:0 auto;padding:20px} section{display:none} section.on{display:block}
 h2{font-size:12px;text-transform:uppercase;letter-spacing:.09em;color:var(--mut);margin:26px 0 10px;font-weight:700}
 .strip{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:6px}
 .chip{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:4px 12px;font-size:12px;color:var(--mut)}
 .chip b{color:var(--ink)}
 .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:10px 0}
 /* ---- actionable trade tickets (the hero) ---- */
 .tickets{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
 .ticket{background:var(--panel);border:1px solid var(--line);border-left:6px solid var(--mut);
   border-radius:14px;padding:16px 18px;box-shadow:0 1px 3px #0000000d}
 .ticket.long{border-left-color:var(--long)} .ticket.short{border-left-color:var(--short)}
 .ticket .thead{display:flex;align-items:center;gap:10px;margin-bottom:4px}
 .badge{font-weight:800;font-size:18px;letter-spacing:.03em;padding:2px 10px;border-radius:8px}
 .badge.long{color:var(--long);background:var(--long-bg)} .badge.short{color:var(--short);background:var(--short-bg)}
 .thead .sym{font-weight:700;font-size:16px} .thead .tf{color:var(--mut);font-size:12px}
 .pill{padding:2px 9px;border-radius:999px;font-size:11px;font-weight:800;letter-spacing:.03em}
 .pill.place{background:var(--warn-bg);color:var(--warn)} .pill.in{background:var(--info-bg);color:var(--info)}
 .instr{color:var(--mut);font-size:12.5px;margin:10px 0 8px}
 .levels{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:6px 0 10px}
 .lvl{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:9px 10px;text-align:center}
 .lvl span{display:block;font-size:10.5px;letter-spacing:.08em;color:var(--mut);text-transform:uppercase;margin-bottom:3px}
 .lvl b{font-size:20px} .lvl.stop b{color:var(--short)} .lvl.tgt b{color:var(--long)}
 .ticket .meta{font-size:12px;color:var(--mut)} .ticket .meta b{color:var(--ink)}
 .empty{border-left-color:var(--line);color:var(--mut);text-align:center;padding:26px}
 /* ---- kpis ---- */
 .kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px}
 .kpi{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
 .kpi span{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
 .kpi b{display:block;font-size:22px;margin-top:4px} .pos{color:var(--long)} .neg{color:var(--short)}
 /* ---- tables ---- */
 .scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
 table{border-collapse:collapse;width:100%;font-size:12.5px} th,td{padding:7px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
 th{color:var(--mut);font-weight:600;text-transform:uppercase;font-size:10.5px;letter-spacing:.05em;background:var(--panel2)}
 th:first-child,td:first-child{text-align:left} tr:last-child td{border-bottom:none}
 .tag{padding:1px 8px;border-radius:6px;font-size:11px;font-weight:700}
 .tag.TAKE{background:var(--long-bg);color:var(--long)} .tag.SKIP{background:var(--warn-bg);color:var(--warn)}
 .tag.NO_SETUP{background:var(--panel2);color:var(--mut)}
 .legend{color:var(--mut);font-size:12px;margin:6px 2px}
 /* ---- controls (replay) ---- */
 .row{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-end;margin:10px 0}
 label.fld{display:flex;flex-direction:column;font-size:12px;color:var(--mut);gap:5px}
 input,select{font:inherit;padding:7px 10px;border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--ink)}
 .sym{font-size:14px;margin-right:14px} button{font:inherit;padding:9px 18px;border:none;border-radius:9px;
   background:var(--accent);color:#fff;font-weight:700;cursor:pointer} button:disabled{opacity:.5;cursor:default}
 .prog{height:8px;background:var(--line);border-radius:6px;overflow:hidden;width:200px;display:inline-block;vertical-align:middle}
 .prog>i{display:block;height:100%;background:var(--accent)} a.dl{color:var(--accent);font-weight:700;text-decoration:none}
 .mut{color:var(--mut);font-size:12px}
</style>
<header>
  <span class=brand>ict&nbsp;live</span><span class=sub>trading dashboard</span>
  <nav><a id=nav-live class=on onclick="show('live')">LIVE</a>
       <a id=nav-replay onclick="show('replay')">REPLAY</a></nav>
  <span class=conn><span id=cdot class=dot></span><span id=livestate>connecting…</span></span>
</header>
<main>
<section id=live class=on><div id=live-body><p class=mut>connecting to live service…</p></div></section>
<section id=replay>
  <div class=card>
    <h2 style="margin-top:0">run a simulation</h2>
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
function fmt(v){return (v===null||v===undefined||v==='')?'—':v;}
function num(v){return (v===null||v===undefined)?'—':v;}
function cls(v){return (typeof v==='number')?(v>0?'pos':(v<0?'neg':'')):'';}

// ---------------- LIVE ----------------
function tickets(rep){
  const opens=rep.open_trades||[]; const byId={}; (rep.recent_signals||[]).forEach(s=>byId[s.ticket_id]=s);
  if(!opens.length) return '<div class="ticket empty">No active trade right now — the engine is waiting for the next valid setup.<br><span class=mut>A ticket appears here the moment a TAKE signal fires.</span></div>';
  return '<div class=tickets>'+opens.map(o=>{
    const sig=byId[o.ticket_id]||{}; const long=o.direction==='long'; const dir=long?'LONG':'SHORT';
    const risk=Math.abs(o.entry-o.stop);
    const status=o.status==='OPEN'?'<span class="pill in">IN TRADE</span>':'<span class="pill place">PLACE ORDER</span>';
    const how=o.status==='OPEN'?'You should be in this position:':'Place a limit order on Topstep:';
    return `<div class="ticket ${o.direction}">
      <div class=thead><span class="badge ${o.direction}">${dir}</span><span class=sym>${o.symbol}</span>${status}</div>
      <div class=instr>${how}</div>
      <div class=levels>
        <div class=lvl><span>Entry</span><b class=num>${num(o.entry)}</b></div>
        <div class="lvl stop"><span>Stop</span><b class=num>${num(o.stop)}</b></div>
        <div class="lvl tgt"><span>Target +2R</span><b class=num>${num(o.exit_target)}</b></div>
      </div>
      <div class=meta>risk <b class=num>${risk.toFixed(2)}</b> pts (1R) · reward +2R · struct target <span class=num>${num(o.structural_target)}</span>
        · exec score <span class=num>${num(o.execution_score!=null?o.execution_score:sig.confidence)}</span> · weakest ${fmt(o.weakest_factor||sig.weakest_factor)}</div>
    </div>`;}).join('')+'</div>';
}
function kpis(s,opens){
  const t=[['trades',num(s.scored)],['win rate',num(s.win_rate)],['expectancy R',num(s.expectancy_R)],
    ['total R',num(s.total_R)],['profit factor',num(s.profit_factor)],['max DD R',num(s.max_drawdown_R)],
    ['open',opens]];
  return '<div class=kpis>'+t.map(([k,v])=>`<div class=kpi><span>${k}</span><b class="${cls(typeof v==='number'?v:0)}">${v}</b></div>`).join('')+'</div>';
}
function liveTables(rep){
  const s=rep.closed_summary||{}, h=rep.health||{};
  const strip=`<div class=strip>
    <span class=chip>signal <b>${fmt(h.signal_tf)}</b></span><span class=chip>entry <b>${fmt(h.entry_tf)}</b></span>
    <span class=chip>last bar <b>${fmt(Object.values(h.last_signal_bar||{})[0])}</b></span>
    <span class=chip>open <b>${(rep.open_trades||[]).length}</b></span>
    <span class=chip>closed <b>${(s.scored!=null?s.scored:0)}</b></span></div>`;
  const closedR=(rep.closed_trades||[]).map(c=>`<tr><td>${fmt(c.close_time)}</td><td>${c.symbol}</td>
    <td>${c.direction}</td><td>${c.result}</td><td class="num ${cls(c.result_R)}">${num(c.result_R)}</td>
    <td class=num>${num(c.mfe_R)}</td><td class=num>${num(c.mae_R)}</td><td class=num>${num(c.bars_held)}</td></tr>`).join('')
    ||'<tr><td colspan=8 class=mut>no closed trades yet</td></tr>';
  const sigR=(rep.recent_signals||[]).map(x=>{const r=x.reasoning||{};return `<tr>
    <td>${fmt(x.time)}</td><td>${x.symbol}</td><td><span class="tag ${x.action}">${x.action}</span></td>
    <td>${fmt(x.structural)}</td><td class=num>${num(x.entry)}</td><td class=num>${num(x.stop)}</td>
    <td class=num>${num(x.exit_target)}</td><td class=num>${num(x.confidence)}</td><td>${fmt(x.weakest_factor)}</td>
    <td>${fmt(r.manipulation)}</td><td>${fmt(r.mss)}</td><td>${fmt(r.fvg)}</td><td>${fmt(r.dealing_range)}</td></tr>`;}).join('')
    ||'<tr><td colspan=13 class=mut>no signals yet</td></tr>';
  return `<h2>Actionable now</h2>${tickets(rep)}
    <h2>Performance (closed trades)</h2>${kpis(s,(rep.open_trades||[]).length)}
    <h2>Closed trades</h2><div class=scroll><table>
      <tr><th>closed<th>symbol<th>dir<th>result<th>R<th>MFE<th>MAE<th>bars</tr>${closedR}</table></div>
    <h2>Signal log</h2>
    <div class=legend><span class="tag TAKE">TAKE</span> = a trade ticket (shown above).
      <span class="tag SKIP">SKIP</span> = valid setup the execution filter rejected.
      <span class="tag NO_SETUP">NO_SETUP</span> = no valid setup this bar. Only TAKE is actionable.</div>
    <div class=scroll><table>
      <tr><th>time<th>sym<th>action<th>dir<th>entry<th>stop<th>+2R<th>score<th>weakest<th>manipulation<th>MSS<th>FVG<th>dealing range</tr>
      ${sigR}</table></div>
    <div class=strip style="margin-top:14px"><span class=chip>engine 1H·15m · last ${fmt(Object.values(h.last_signal_bar||{})[0])}</span></div>`;
}
async function pollLive(){
  try{
    const d=await (await fetch('/live')).json();
    if(!d.connected){$('#cdot').className='dot no';$('#livestate').textContent='live service offline';
      $('#live-body').innerHTML=`<div class="card"><h2 style=margin-top:0>Live service not connected</h2>
      <p class=mut>Start it with <code>python -m ict_live.live.serve</code> and (for data) the feed bridge,
      then this fills in automatically.<br>${fmt(d.error)}</p></div>`;return;}
    $('#cdot').className='dot ok';$('#livestate').textContent='live · connected';
    $('#live-body').innerHTML=liveTables(d.report);
  }catch(e){$('#cdot').className='dot no';$('#livestate').textContent='poll error';}
}
setInterval(pollLive,4000);pollLive();

// ---------------- REPLAY ----------------
const COLS=[["scored","trades"],["win_rate","win%"],["expectancy_R","exp R"],["profit_factor","PF"],
 ["max_drawdown_R","maxDD"],["total_R","totR"],["longest_win_streak","Wstk"],["longest_loss_streak","Lstk"],
 ["avg_hold_min","avgHold"],["median_hold_min","medHold"]];
function statTable(agg){
  let h="<tr><th>period</th>"+COLS.map(c=>"<th>"+c[1]+"</th>").join("")+"</tr>";
  let rows="<tr><td>OVERALL</td>"+COLS.map(c=>'<td class=num>'+fmt(agg.overall[c[0]])+'</td>').join("")+"</tr>";
  for(const k of Object.keys(agg.periods||{}))
    rows+="<tr><td>"+k+"</td>"+COLS.map(c=>'<td class=num>'+fmt(agg.periods[k][c[0]])+'</td>').join("")+"</tr>";
  return "<div class=scroll><table>"+h+rows+"</table></div>";
}
function renderReplay(job){
  let html="";
  if(job.state==="error")html+='<div class=card style="border-color:var(--short)"><b>Error:</b> '+fmt(job.error)+'</div>';
  for(const sym of job.symbols){
    const p=job.progress[sym]||{}, pct=p.total?Math.round(100*p.done/p.total):0;
    const r=job.results[sym], csv=job.csv[sym];
    html+='<div class=card><h2 style=margin-top:0>'+sym+'</h2>';
    if(!r)html+='<div class=prog><i style=width:'+pct+'%></i></div> <span class=mut>'+pct+'%</span>';
    else{html+='<div class=mut>'+r.bars_5m+' 5m bars · '+r.signals+' signals · '+(csv?csv.trades:0)+' trades</div>'+statTable(r);
      if(csv)html+="<p><a class=dl href='/download?job="+job.id+"&symbol="+encodeURIComponent(sym)+"'>⬇ download trades CSV</a></p>";}
    html+='</div>';
  }
  $("#replay-out").innerHTML=html;
}
let timer=null;
async function pollReplay(id){
  const job=await (await fetch("/status?job="+id)).json();renderReplay(job);
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
