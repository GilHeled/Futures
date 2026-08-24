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
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

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
 /* ---- feed panel (data source + per-symbol streaming, merged) ---- */
 .feedhead{display:flex;align-items:center;gap:11px;padding-bottom:13px;border-bottom:1px solid var(--line)}
 .feedname{font-weight:700;color:var(--ink);font-size:14px}
 .feedsub{color:var(--mut);font-size:12px;margin-top:2px}
 .fdot{width:10px;height:10px;border-radius:50%;background:var(--mut);flex:none}
 .fdot.ok{background:var(--long);box-shadow:0 0 0 3px var(--long-bg)}
 .fdot.no{background:var(--warn);box-shadow:0 0 0 3px var(--warn-bg)}
 @media(prefers-reduced-motion:no-preference){.fdot.ok{animation:pulse 2s ease-in-out infinite}}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
 .feedgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(232px,1fr));gap:8px;margin-top:12px}
 .feedrow{display:flex;align-items:center;gap:9px;padding:9px 12px;border:1px solid var(--line);
   border-radius:10px;background:var(--panel2);cursor:pointer;transition:border-color .12s,background .12s;user-select:none}
 .feedrow:hover{border-color:var(--mut)}
 .feedrow.on{border-color:var(--accent)}
 @supports(background:color-mix(in srgb,red,blue)){.feedrow.on{background:color-mix(in srgb,var(--accent) 9%,var(--panel2))}}
 .feedrow input{position:absolute;opacity:0;width:0;height:0}
 .rdot{width:8px;height:8px;border-radius:50%;flex:none;display:inline-block;background:#5b6474}
 .rdot.ok{background:var(--long)} .rdot.stale{background:var(--warn)} .rdot.wait{background:var(--info)}
 .rsym{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-weight:600;font-size:12.5px;color:var(--ink)}
 .rname{color:var(--mut);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .rage{margin-left:auto;font-family:"SF Mono",ui-monospace,Menlo,monospace;font-size:11px;color:var(--mut);flex:none}
 .rage.ok{color:var(--long)} .rage.stale{color:var(--warn)}
 .feednote{color:var(--mut);font-size:11.5px;margin-top:12px;line-height:1.6}
 .feednote .rdot{margin:0 3px -1px 8px}
 /* ---- per-symbol current read ---- */
 .reads{display:grid;grid-template-columns:repeat(auto-fill,minmax(258px,1fr));gap:10px}
 .read{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--mut);
   border-radius:12px;padding:11px 13px}
 .read.long{border-left-color:var(--long)} .read.short{border-left-color:var(--short)}
 .read.flat{border-left-color:var(--line)}
 .read .rhd{display:flex;align-items:center;gap:8px}
 .read .rsy{font-family:"SF Mono",ui-monospace,Menlo,monospace;font-weight:700;font-size:13px}
 .read .rnn{color:var(--mut);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .read .rtag{margin-left:auto}
 .read .rln{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:9px;font-size:12px;color:var(--mut)}
 .read .rln b{color:var(--ink);font-weight:600}
 .read .bias.long{color:var(--long)} .read .bias.short{color:var(--short)}
 .read .why{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
 .read .why .wc{background:var(--panel2);border:1px solid var(--line);border-radius:7px;
   padding:2px 7px;font-size:10.5px;color:var(--mut)} .read .why .wc b{color:var(--ink);font-weight:600}
 .read .rfoot{margin-top:9px;font-size:10.5px;color:var(--mut);display:flex;align-items:center;gap:8px}
 .whybtn{background:var(--panel2);color:var(--accent);border:1px solid var(--line);border-radius:7px;
   padding:3px 10px;font-size:11px;font-weight:700;cursor:pointer;margin-left:auto} .whybtn:hover{border-color:var(--accent)}
 .tctl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0 2px}
 .tbtn{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;
   padding:6px 13px;font-size:12px;font-weight:700;cursor:pointer} .tbtn:hover{border-color:var(--accent)}
 .tbtn.go{color:var(--long);border-color:color-mix(in srgb,var(--long) 45%,var(--line))}
 .tbtn.no{color:var(--short);border-color:color-mix(in srgb,var(--short) 45%,var(--line))}
 .placedtag{background:var(--info-bg);color:var(--info);border-radius:999px;padding:3px 10px;font-size:11px;font-weight:800}
 .decisions{color:var(--mut);font-size:12px;margin-top:10px}
 .v2dec{border-radius:999px;padding:2px 10px;font-size:11px;font-weight:800;letter-spacing:.03em;
   background:var(--panel2);color:var(--mut);border:1px solid var(--line)}
 .v2dec.long{color:var(--long);background:var(--long-bg);border-color:transparent}
 .v2dec.short{color:var(--short);background:var(--short-bg);border-color:transparent}
 .modal{position:fixed;inset:0;background:#000a;display:none;z-index:50;padding:22px}
 .modal.on{display:flex} .modal-box{background:var(--panel);border:1px solid var(--line);border-radius:14px;
   margin:auto;width:min(1120px,96vw);height:90vh;display:flex;flex-direction:column;overflow:hidden}
 .modal-hd{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line)}
 .modal-hd b{font-size:13px;font-family:"SF Mono",ui-monospace,Menlo,monospace}
 .modal-hd .x{margin-left:auto;cursor:pointer;font-size:16px;color:var(--mut);background:none;border:none;padding:2px 8px}
 .modal iframe{flex:1;border:0;width:100%}
 .modal .imgwrap{flex:1;overflow:auto;display:flex;background:var(--panel2)}
 .modal .imgwrap img{max-width:100%;margin:auto}
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
 .ticket.stale{border-left-color:var(--warn)}
 .warn{background:var(--warn-bg);color:var(--warn);border-radius:10px;padding:9px 11px;font-size:12px;margin:2px 0 8px;font-weight:600}
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
 button.preset{background:var(--panel2);color:var(--ink);border:1px solid var(--line);padding:6px 12px;
   font-size:12px;font-weight:600} button.preset:hover{border-color:var(--accent)}
 #presets{gap:8px;align-items:center;margin:2px 0 4px}
 .prog{height:8px;background:var(--line);border-radius:6px;overflow:hidden;width:200px;display:inline-block;vertical-align:middle}
 .prog>i{display:block;height:100%;background:var(--accent)} a.dl{color:var(--accent);font-weight:700;text-decoration:none}
 .mut{color:var(--mut);font-size:12px}
</style>
<header>
  <span class=brand>ict&nbsp;live</span><span class=sub>trading dashboard</span>
  <nav><a id=nav-live class=on onclick="show('live')">LIVE</a>
       <a id=nav-replay onclick="show('replay')">REPLAY</a>
       <a id=nav-v2 onclick="show('v2')">V2 &#9879;</a></nav>
  <span class=conn><span id=cdot class=dot></span><span id=livestate>connecting…</span></span>
</header>
<main>
<section id=live class=on><div id=live-body><p class=mut>connecting to live service…</p></div></section>
<section id=v2><div id=v2-body><p class=mut>connecting to v2 (experimental) service…</p></div></section>
<section id=replay>
  <div class=card>
    <h2 style="margin-top:0">run a simulation</h2>
    <div class=row id=syms>__OPTS__</div>
    <div class=row id=presets><span class=mut>quick range:</span>
      <button class=preset data-r="1m">last month</button>
      <button class=preset data-r="3m">last 3 months</button>
      <button class=preset data-r="ytd">YTD</button>
      <button class=preset data-r="2025">2025</button>
      <button class=preset data-r="2024">2024</button>
      <button class=preset data-r="full">full range</button></div>
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
<div id=rmodal class=modal><div class=modal-box>
  <div class=modal-hd><b id=rmodal-title>Reasoning</b><button class=x onclick="closeReasoning()">✕ close</button></div>
  <iframe id=rmodal-frame title="reasoning inspector"></iframe></div></div>
<div id=cmodal class=modal><div class=modal-box>
  <div class=modal-hd><b id=cmodal-title>Chart</b><button class=x onclick="closeChart()">✕ close</button></div>
  <div class=imgwrap><img id=cmodal-img alt="chart"></div></div></div>
<script>
const $=s=>document.querySelector(s);
function show(t){for(const s of ['live','replay','v2']){$('#'+s).classList.toggle('on',s===t);$('#nav-'+s).classList.toggle('on',s===t);}}
function fmt(v){return (v===null||v===undefined||v==='')?'—':v;}
function num(v){return (v===null||v===undefined)?'—':v;}
function cls(v){return (typeof v==='number')?(v>0?'pos':(v<0?'neg':'')):'';}
// Format a PRICE for display: snap to the instrument's tick and show its natural decimals, so the
// dashboard never shows an un-placeable float like 69.34250259399414 (Silver ticks are 0.005).
function price(v,sym,rep){
  if(v===null||v===undefined||v==='') return '—';
  const n=Number(v); if(!isFinite(n)) return fmt(v);
  const t=((rep&&rep.instrument_ticks)||{})[sym];
  if(!t) return String(Math.round(n*100)/100);
  const d=(String(t).split('.')[1]||'').length;
  return (Math.round(n/t)*t).toFixed(d);
}

// ---------------- LIVE ----------------
function tickets(rep){
  const all=rep.open_trades||[]; const byId={}; (rep.recent_signals||[]).forEach(s=>byId[s.ticket_id]=s);
  const ctrl=rep.control||{}; const stOf=id=>(ctrl[id]||{}).status||null;
  const cvals=Object.values(ctrl), cnt=s=>cvals.filter(c=>c.status===s).length;
  const summary=cvals.length?`<div class=decisions>Your decisions — placed <b>${cnt('placed')}</b> · skipped <b>${cnt('skipped')}</b> · cancelled <b>${cnt('cancelled')}</b> · closed <b>${cnt('closed')}</b></div>`:'';
  const opens=all.filter(o=>{const s=stOf(o.ticket_id);return s===null||s==='placed';});   // actionable = undecided or placed
  if(!opens.length) return '<div class="ticket empty">No active trade right now — the engine is waiting for the next valid setup.<br><span class=mut>A ticket appears here the moment a TAKE signal fires.</span></div>'+summary;
  const now=rep.server_time_ms||Date.now(); const lastp=rep.last_price||{};
  return '<div class=tickets>'+opens.map(o=>{
    const st=stOf(o.ticket_id);
    const sig=byId[o.ticket_id]||{}; const long=o.direction==='long'; const dir=long?'LONG':'SHORT';
    const risk=Math.abs(o.entry-o.stop);
    const status=o.status==='OPEN'?'<span class="pill in">IN TRADE</span>':'<span class="pill place">PLACE ORDER</span>';
    const how=o.status==='OPEN'?'You should be in this position:':'Place a limit order on Topstep:';
    // Freshness guard: how far the entry sits from the live price, and how old the setup is. A
    // PENDING (unfilled) ticket far from price is a resting limit, not an at-market order.
    let guard=''; let warn=false; const lp=lastp[o.symbol];
    if(lp!=null){
      const dpts=o.entry-lp, dpct=Math.abs(dpts)/lp*100, side=dpts>0?'above':'below';
      if(o.status!=='OPEN' && dpct>0.5) warn=true;
      guard+=`<span class=chip>price now <b class=num>${price(lp,o.symbol,rep)}</b></span>`+
             `<span class=chip>entry <b class=num>${Math.abs(dpts).toFixed(2)}</b> pts ${side} market (<b>${dpct.toFixed(2)}%</b>)</span>`;
    }
    const opened=o.opened_time?Date.parse(o.opened_time):NaN;
    if(!isNaN(opened)){
      const am=Math.max(0,Math.round((now-opened)/60000));
      if(o.status!=='OPEN' && am>240) warn=true;
      guard+=`<span class=chip>setup age <b>${am>=120?Math.round(am/60)+'h':am+'m'}</b></span>`;
    }
    const banner=warn?'<div class=warn>⚠ Resting limit far from price / aging setup — NOT an at-market order. It fills only if price returns to the entry. Verify it still makes sense before placing.</div>':'';
    return `<div class="ticket ${o.direction}${warn?' stale':''}">
      <div class=thead><span class="badge ${o.direction}">${dir}</span><span class=sym>${o.symbol}</span>${status}
        ${(rep.charts||{})[o.symbol]?`<button class=whybtn onclick="openChart('${o.symbol}')">chart</button>`:''}
        <button class=whybtn onclick="openReasoning('${o.symbol}')">why?</button></div>
      <div class=instr>${how}</div>
      <div class=levels>
        <div class=lvl><span>Entry</span><b class=num>${price(o.entry,o.symbol,rep)}</b></div>
        <div class="lvl stop"><span>Stop</span><b class=num>${price(o.stop,o.symbol,rep)}</b></div>
        <div class="lvl tgt"><span>Target +2R</span><b class=num>${price(o.exit_target,o.symbol,rep)}</b></div>
      </div>
      ${guard?'<div class=strip style="margin:2px 0 8px">'+guard+'</div>':''}
      ${banner}
      <div class=meta>risk <b class=num>${risk.toFixed(2)}</b> pts (1R) · reward +2R · struct target <span class=num>${price(o.structural_target,o.symbol,rep)}</span>
        · exec score <span class=num>${o.execution_score!=null?Number(o.execution_score).toFixed(2):num(sig.confidence)}</span> · weakest ${fmt(o.weakest_factor||sig.weakest_factor)}</div>
      ${st==='placed'
        ? `<div class=tctl><span class=placedtag>✓ you placed this</span><button class="tbtn no" onclick="tradeControl('${o.ticket_id}','${o.status==='OPEN'?'closed':'cancelled'}')">${o.status==='OPEN'?'Close trade':'Cancel pending'}</button></div>`
        : `<div class=tctl><button class="tbtn go" onclick="tradeControl('${o.ticket_id}','placed')">I placed this</button><button class=tbtn onclick="tradeControl('${o.ticket_id}','skipped')">Skip</button></div>`}
    </div>`;}).join('')+'</div>'+summary;
}
async function tradeControl(id,status){
  try{await fetch('/live/trade-control',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ticket_id:id,status:status})});}catch(e){}
  pollLive();
}
function kpis(s,opens){
  const t=[['trades',num(s.scored)],['win rate',num(s.win_rate)],['expectancy R',num(s.expectancy_R)],
    ['total R',num(s.total_R)],['profit factor',num(s.profit_factor)],['max DD R',num(s.max_drawdown_R)],
    ['open',opens]];
  return '<div class=kpis>'+t.map(([k,v])=>`<div class=kpi><span>${k}</span><b class="${cls(typeof v==='number'?v:0)}">${v}</b></div>`).join('')+'</div>';
}
// Merged "Feed" card: the data source + heartbeat on top, then one row per symbol combining its
// streaming toggle with a live freshness dot + last-bar age (previously two separate sections).
function feedPanel(rep){
  const f=rep.feed; const inst=rep.instruments||[]; const nm=rep.instrument_names||{};
  const now=Date.now();
  const active = (rep.enabled!=null) ? rep.enabled : ((f&&f.symbols)||[]);
  const bars=(f&&f.bars)||{};
  const beat=(f&&f.received_ms!=null)?Math.round((now-f.received_ms)/1000):null;
  const live = f && beat!=null && beat<90;                          // heartbeat fresh within 90s
  const sub = f ? ('heartbeat '+(beat==null?'—':beat+'s ago')+' · '+active.length+' of '+inst.length+' symbols streaming')
                : 'start a real-time (MCP) or yfinance feed';
  const head = '<div class=feedhead><span class="fdot '+(f?(live?'ok':'no'):'')+'"></span>'+
    '<div><div class=feedname>'+(f?fmt(f.source):'No feed connected')+'</div>'+
    '<div class=feedsub>'+sub+'</div></div></div>';
  if(!inst.length) return '<h2>Feed</h2><div class=card>'+head+'</div>';
  const rows=inst.map(s=>{
    const on=active.includes(s);
    const t=bars[s]; const a=(on&&t)?Math.round((now-t)/60000):null;
    const state = !on ? 'off' : (a==null ? 'wait' : (a<=3 ? 'ok' : 'stale'));
    const age   = !on ? 'off' : (a==null ? '…' : (a<1 ? '<1m' : a+'m ago'));
    return '<label class="feedrow '+(on?'on':'')+'">'+
      '<input type=checkbox '+(on?'checked':'')+' value="'+s+'" onchange="postControl()">'+
      '<span class="rdot '+state+'"></span>'+
      '<span class=rsym>'+s+'</span>'+
      '<span class=rname>'+fmt(nm[s]||'')+'</span>'+
      '<span class="rage '+state+'">'+age+'</span></label>';}).join('');
  return '<h2>Feed</h2><div class=card>'+head+
    '<div class=feedgrid id=toggles>'+rows+'</div>'+
    '<div class=feednote>Click a symbol to start or stop streaming it. Freshness: '+
    '<span class="rdot ok"></span> live · <span class="rdot stale"></span> stale (&gt;3m) · '+
    '<span class="rdot off"></span> off. The real-time TradingView feed round-robins one chart, '+
    'so more symbols means a slower cycle.</div></div>';
}
async function postControl(){
  const en=[...document.querySelectorAll('#toggles input:checked')].map(c=>c.value);
  try{await fetch('/live/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:en})});}catch(e){}
}
function currentRead(rep){
  const nm=rep.instrument_names||{};
  const active=(rep.enabled!=null)?rep.enabled:(((rep.feed||{}).symbols)||[]);
  // latest signal per symbol: prefer the service's per-symbol map, else newest-first recent_signals
  let cur=Object.assign({},rep.current||{});
  (rep.recent_signals||[]).forEach(x=>{ if(x&&x.symbol&&!(x.symbol in cur)) cur[x.symbol]=x; });
  const syms=(active.length?active:Object.keys(cur)).slice().sort();
  if(!syms.length) return '<div class="card mut">No symbols streaming yet — enable one in the Feed card above.</div>';
  const now=rep.server_time_ms||Date.now(); const bars=(rep.feed||{}).bars||{};
  const cards=syms.map(sym=>{
    const c=cur[sym];
    if(!c) return `<div class="read flat"><div class=rhd><span class=rsy>${sym}</span>
      <span class=rnn>${fmt(nm[sym]||'')}</span></div>
      <div class=rfoot>awaiting first 1H close…</div></div>`;
    const st=(c.structural||'').toUpperCase();
    const side=st.indexOf('LONG')>=0?'long':(st.indexOf('SHORT')>=0?'short':'flat');
    const r=c.reasoning||{};
    const why=[['manip',r.manipulation],['MSS',r.mss],['FVG',r.fvg],['DR',r.dealing_range]]
      .filter(([k,v])=>v!=null&&v!=='').map(([k,v])=>`<span class=wc>${k} <b>${fmt(v)}</b></span>`).join('');
    const t=bars[sym]; const a=t?Math.round((now-t)/60000):null;
    const age=a==null?'':(a<1?'<1m ago':a+'m ago');
    const execTxt=(c.execution&&c.execution!=='N/A')?`exec <b>${fmt(c.execution)}</b>`
      +(c.confidence!=null?` (${num(c.confidence)})`:''):'';
    return `<div class="read ${side}">
      <div class=rhd><span class=rsy>${sym}</span><span class=rnn>${fmt(nm[sym]||'')}</span>
        <span class="tag ${c.action} rtag">${fmt(c.action)}</span></div>
      <div class=rln><span class="bias ${side}">bias <b>${fmt(c.structural)}</b></span>
        ${execTxt?'<span>'+execTxt+'</span>':''}
        ${c.weakest_factor?'<span>weakest <b>'+fmt(c.weakest_factor)+'</b></span>':''}</div>
      ${why?'<div class=why>'+why+'</div>':''}
      <div class=rfoot><span>1H bar ${fmt(c.time)}${age?' · updated '+age:''}</span>
        ${(rep.charts||{})[sym]?`<button class=whybtn onclick="openChart('${sym}')">chart</button>`:''}
        <button class=whybtn onclick="openReasoning('${sym}')">why?</button></div>
    </div>`;}).join('');
  return '<div class=reads>'+cards+'</div>';
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
    <td>${fmt(x.structural)}</td><td class=num>${price(x.entry,x.symbol,rep)}</td><td class=num>${price(x.stop,x.symbol,rep)}</td>
    <td class=num>${price(x.exit_target,x.symbol,rep)}</td><td class=num>${x.confidence!=null?Number(x.confidence).toFixed(2):'—'}</td><td>${fmt(x.weakest_factor)}</td>
    <td>${fmt(r.manipulation)}</td><td>${fmt(r.mss)}</td><td>${fmt(r.fvg)}</td><td>${fmt(r.dealing_range)}</td></tr>`;}).join('')
    ||'<tr><td colspan=13 class=mut>no signals yet</td></tr>';
  return `${feedPanel(rep)}
    <h2>Current read</h2>${currentRead(rep)}
    <h2>Actionable now</h2>${tickets(rep)}
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
function openReasoning(sym){$('#rmodal-title').textContent=sym+' — reasoning';
  $('#rmodal-frame').src='/live/reasoning?symbol='+encodeURIComponent(sym);$('#rmodal').classList.add('on');}
function closeReasoning(){$('#rmodal').classList.remove('on');$('#rmodal-frame').src='about:blank';}
function openChart(sym){$('#cmodal-title').textContent=sym+' — chart';
  $('#cmodal-img').src='/live/chart?symbol='+encodeURIComponent(sym)+'&t='+Date.now();$('#cmodal').classList.add('on');}
function closeChart(){$('#cmodal').classList.remove('on');$('#cmodal-img').removeAttribute('src');}
$('#rmodal').onclick=e=>{if(e.target.id==='rmodal')closeReasoning();};
$('#cmodal').onclick=e=>{if(e.target.id==='cmodal')closeChart();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeReasoning();closeChart();}});
setInterval(pollLive,4000);pollLive();

// ---------------- V2 (experimental, advisory) ----------------
function v2Tables(rep){
  const syms=rep.symbols||{}; const names=Object.keys(syms).sort();
  const banner='<div class=warn>&#9879; V2 (experimental) — ICT cascade 4H context → 1H setup → 15m confirmation → 1m execution, side-by-side with v1, ADVISORY ONLY, not validated.</div>';
  if(!names.length) return banner+'<div class="card mut">No v2 data yet — waiting for the shared feed to accumulate bars.</div>';
  const sideOf=d=>d==='long'?'long':(d==='short'?'short':'flat');
  const cards=names.map(sym=>{
    const s=syms[sym], c=s.context||{}, st=s.setup||{}, cf=s.confirmation||{}, e=s.execution||{}, u=s.updated||{}, tf=s.timeframes||{};
    const dr=c.dealing_range; const drs=dr?`${num(dr.low)}–${num(dr.high)} CE ${num(dr.ce)} (${dr.direction})`:'—';
    const obj=c.liquidity_objective; const objs=obj?`${obj.kind==='high'?'BSL':'SSL'} ${num(obj.price)}`:'—';
    // each stage colored by ITS OWN state; neutral grey otherwise so color never implies a phantom trade
    const biasSide=sideOf(c.bias);
    const setupSide=(st.gated>0)?sideOf(st.direction):'flat';
    const confSide=(cf.gated>0)?sideOf(cf.direction):'flat';
    const top=e.top; const execSide=top?sideOf(top.direction):'flat';
    const dec=top?execSide.toUpperCase():'NO-TRADE';
    const execLine=top?`${top.direction.toUpperCase()} entry ${num(top.entry)} · stop ${num(top.stop)} · target ${num(top.target)}${top.ltf_confirmed?' · ✓':''}`:fmt(e.decision);
    const gline=(x)=>`gated <b>${fmt(x.gated)}</b> of ${fmt(x.candidates)}${x.gated>0?' · '+fmt(x.direction):''}`;
    return `<div class="ticket ${execSide}">
      <div class=thead><span class=sym>${sym}</span><span class="v2dec ${execSide}">${dec}</span></div>
      <div class=reads>
        <div class="read ${biasSide}"><div class=rhd><span class=rsy>4H</span><span class=rnn>context</span></div>
          <div class=why><span class=wc>bias <b>${fmt(c.bias)}</b></span><span class=wc>range <b>${drs}</b></span><span class=wc>draw <b>${objs}</b></span></div>
          <div class=rfoot>updated ${fmt(u[tf.context])}</div></div>
        <div class="read ${setupSide}"><div class=rhd><span class=rsy>1H</span><span class=rnn>setup</span></div>
          <div class=rln>${gline(st)}</div>
          <div class=rfoot>updated ${fmt(u[tf.setup])}</div></div>
        <div class="read ${confSide}"><div class=rhd><span class=rsy>15m</span><span class=rnn>confirmation</span></div>
          <div class=rln>${gline(cf)}</div>
          <div class=rfoot>updated ${fmt(u[tf.confirm])}</div></div>
        <div class="read ${execSide}"><div class=rhd><span class=rsy>1m</span><span class=rnn>execution</span></div>
          <div class=rln>${execLine}</div>
          <div class=rfoot>updated ${fmt(u[tf.trigger])}</div></div>
      </div></div>`;}).join('');
  return banner+'<div class=tickets>'+cards+'</div>';
}
async function pollV2(){
  try{const d=await (await fetch('/v2/report')).json();$('#v2-body').innerHTML=v2Tables(d);}
  catch(e){$('#v2-body').innerHTML='<div class="card mut">v2 (experimental) service not reachable — start it with <code>python -m ict_v2.serve</code>.</div>';}
}
setInterval(pollV2,5000);pollV2();

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
const RKPI=[["scored","trades"],["win_rate","win %"],["expectancy_R","exp R"],["total_R","total R"],
 ["profit_factor","profit factor"],["max_drawdown_R","max DD R"]];
function replayKpis(o){
  o=o||{};
  return '<div class=kpis>'+RKPI.map(([k,l])=>{const v=o[k];
    const c=k==="max_drawdown_R"?(v>0?"neg":""):cls(typeof v==="number"?v:0);
    return `<div class=kpi><span>${l}</span><b class="${c}">${num(v)}</b></div>`;}).join("")+'</div>';
}
function renderReplay(job){
  let html="";
  if(job.state==="error")html+='<div class=card style="border-color:var(--short)"><b>Error:</b> '+fmt(job.error)+'</div>';
  for(const sym of job.symbols){
    const p=job.progress[sym]||{}, pct=p.total?Math.round(100*p.done/p.total):0;
    const r=job.results[sym], csv=job.csv[sym];
    html+='<div class=card><h2 style=margin-top:0>'+sym+'</h2>';
    if(!r)html+='<div class=prog><i style=width:'+pct+'%></i></div> <span class=mut>'+pct+'% · '+(p.done||0)+'/'+(p.total||'?')+' bars</span>';
    else{html+='<div class=mut style=margin-bottom:10px>'+r.bars_5m+' 5m bars · '+r.signals+' signals · '+(csv?csv.trades:0)+' trades</div>'
      +replayKpis(r.overall)
      +'<details style=margin-top:10px><summary class=mut style=cursor:pointer>per-period breakdown</summary>'+statTable(r)+'</details>';
      if(csv)html+="<p><a class=dl href='/download?job="+job.id+"&symbol="+encodeURIComponent(sym)+"'>⬇ download trades CSV</a></p>";}
    html+='</div>';
  }
  $("#replay-out").innerHTML=html;
}
function setRange(r){
  const pad=n=>String(n).padStart(2,"0"), iso=d=>d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate());
  const to=new Date(); let f=new Date(to), t=to;
  if(r==="1m")f.setMonth(f.getMonth()-1);
  else if(r==="3m")f.setMonth(f.getMonth()-3);
  else if(r==="ytd")f=new Date(to.getFullYear(),0,1);
  else if(r==="2025"){f=new Date(2025,0,1);t=new Date(2025,11,31);}
  else if(r==="2024"){f=new Date(2024,0,1);t=new Date(2024,11,31);}
  else if(r==="full")f=new Date(2019,4,1);
  $("#from").value=iso(f);$("#to").value=iso(t);
}
document.querySelectorAll(".preset").forEach(b=>b.onclick=()=>setRange(b.dataset.r));
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


def make_handler(jobs: JobManager, symbols: list[str], live_fetch=None, live_url=None, v2_url=None):
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
                # no-store so a rebuilt dashboard is picked up immediately (no stale cached page/JS)
                return self._send(200, _page(symbols), "text/html; charset=utf-8",
                                  {"Cache-Control": "no-store, must-revalidate"})
            if u.path == "/live":
                return self._send(200, live_fetch())
            if u.path == "/v2/report":                        # proxy the experimental v2 service
                if not v2_url:
                    return self._send(200, {"symbols": {}, "v2": True})
                try:
                    with urllib.request.urlopen(v2_url.rstrip("/") + "/report", timeout=5) as r:
                        return self._send(200, json.loads(r.read().decode()))
                except Exception as e:
                    return self._send(502, {"error": f"{type(e).__name__}: {e}", "symbols": {}})
            if u.path == "/live/reasoning":                    # proxy the live service's reasoning inspector
                if not live_url:
                    return self._send(503, b"<p>no live url</p>", "text/html; charset=utf-8")
                try:
                    url = live_url.rstrip("/") + "/reasoning?symbol=" + quote(q.get("symbol", [""])[0])
                    with urllib.request.urlopen(url, timeout=6) as r:
                        return self._send(200, r.read(), "text/html; charset=utf-8")
                except Exception as e:
                    return self._send(502, f"<p style='padding:24px;font-family:system-ui'>reasoning unavailable: {type(e).__name__}</p>".encode(), "text/html; charset=utf-8")
            if u.path == "/live/chart":                        # proxy the latest TradingView screenshot for a symbol
                if not live_url:
                    return self._send(404, b"", "image/png")
                try:
                    url = live_url.rstrip("/") + "/chart?symbol=" + quote(q.get("symbol", [""])[0])
                    with urllib.request.urlopen(url, timeout=6) as r:
                        return self._send(200, r.read(), "image/png", {"Cache-Control": "no-store"})
                except Exception:
                    return self._send(404, b"", "image/png")
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
            path = urlparse(self.path).path
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            if path in ("/live/control", "/live/trade-control"):   # forward to the live service
                if not live_url:
                    return self._send(503, {"error": "no live url"})
                target = "/feed/control" if path == "/live/control" else "/trade/control"
                try:
                    req = urllib.request.Request(live_url.rstrip("/") + target, data=raw,
                                                 headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=3) as r:
                        return self._send(200, json.loads(r.read().decode()))
                except Exception as e:
                    return self._send(502, {"error": f"{type(e).__name__}: {e}"})
            if path != "/run":
                return self._send(404, {"error": "not found"})
            body = json.loads(raw or b"{}")
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
    ap.add_argument("--v2-url", default="http://127.0.0.1:8020",
                    help="base URL of the experimental v2 service (for the V2 area)")
    ns = ap.parse_args()
    symbols = REPLAY.available_symbols() or ["MES", "MNQ"]
    jobs = JobManager()
    handler = make_handler(jobs, symbols, live_fetch=lambda: _fetch_live(ns.live_url),
                           live_url=ns.live_url, v2_url=ns.v2_url)
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
