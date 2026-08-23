"""Fast, keyboard-driven annotation loop: queue → inspector → annotate → next scene (<20s/scene).

Serves the active-learning queue in information-value order. Each scene shows the reasoning graph
(deterministic recommendation, ranked competitors, chart-substitute object graph), any PRIOR human
annotation, and whether the scene changed vs a prior engine version. Keyboard:

  L/S/N decision · A accept engine · R reject engine · confidence 1–5 · Enter save+next · . skip · u undo
  execute live: y YES · g NO · location ⇧1–⇧5
  why pass (Batch-2 execution focus): p premium/discount · t too far from CE · j RR misleading ·
            c insufficient confirmation · v fvg location · o other
  error tags (structural — VALIDATED in Batch-1, not the focus): m wrong_manipulation · w wrong_sweep ·
            k wrong_mss · f wrong_fvg · d wrong_dealing_range_context · b bad_location ·
            i insufficient_confirmation · / focus note

On save: append to the human-fidelity dataset (provenance + engine version + scene coords), mark the
scene reviewed, advance to the next highest-information UNREVIEWED scene. Session is persisted →
resumable. Run under the venv:

  .venv/bin/python -m ict_live.research.annotation_app --queue ict_live/research/datasets/queue.jsonl
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ict_live.engine import annotations as anno
from ict_live.engine import pipeline
from ict_live.research import data as data_mod
from ict_live.research import fidelity_ranking as FR
from ict_live.research import rolls as rolls_mod
from ict_live.research import versioning
from ict_live.research.reasoning_html import render

FIDELITY_PATH = "ict_live/research/datasets/human_fidelity.jsonl"
_TF_MIN_STOP = 2.0                       # MES/MNQ: 8 ticks * 0.25 (execution floor)
_bars_cache: dict = {}


def _load_bars(symbol, year):
    key = (symbol, year)
    if key not in _bars_cache:
        _bars_cache[key] = data_mod.load_5m(symbol, start=f"{year-1}-09-01", end=f"{year}-12-31")
    return _bars_cache[key]


def build_state(scene):
    sym, tf, etf = scene["symbol"], scene["signal_tf"], scene.get("entry_tf", "15m")
    year = int(scene["time"][:4])
    bars5 = _load_bars(sym, year)
    for seg in rolls_mod.segment(bars5, rolls_mod.detect_rolls(bars5, sym)):
        if seg.contract != scene["contract"]:
            continue
        sig = data_mod.resample(seg.bars, tf)
        ref_all = data_mod.resample(seg.bars, etf)
        for k, b in enumerate(sig):
            if b.open_time.isoformat() == scene["time"]:
                cc = b.close_time
                ms = pipeline.analyze(sig[max(0, k - 240 + 1):k + 1], tf,
                                      refine_bars=[x for x in ref_all if x.close_time <= cc],
                                      min_stop=_TF_MIN_STOP)
                return ms, b
    return None, None


def _prior(scene_id):
    p = Path(FIDELITY_PATH)
    if not p.exists():
        return None
    last = None
    for line in p.read_text().splitlines():
        r = json.loads(line)
        if r.get("scene_id") == scene_id:
            last = r
    return last


class Session:
    def __init__(self, path, queue):
        self.path, self.queue = path, queue
        self.reviewed, self.deferred, self.history = {}, [], []
        if Path(path).exists():
            d = json.loads(Path(path).read_text())
            self.reviewed = d.get("reviewed", {})
            self.deferred = d.get("deferred", [])
            self.history = d.get("history", [])

    def save(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text(json.dumps({"reviewed": self.reviewed, "deferred": self.deferred,
                                               "history": self.history}, indent=1))

    def next_index(self):
        for i, s in enumerate(self.queue):                       # queue is pre-sorted by info value
            if s["scene_id"] not in self.reviewed and s["scene_id"] not in self.deferred:
                return i
        for i, s in enumerate(self.queue):                       # then deferred
            if s["scene_id"] not in self.reviewed:
                return i
        return None

    def progress(self):
        return {"reviewed": len(self.reviewed), "deferred": len(self.deferred),
                "total": len(self.queue)}


def _panel(scene, idx, prior, prog, version_changed):
    tags = [("m", "wrong_manipulation"), ("w", "wrong_sweep"), ("k", "wrong_mss"),
            ("f", "wrong_fvg"), ("d", "wrong_dealing_range_context"), ("b", "bad_location"),
            ("i", "insufficient_confirmation")]
    tag_html = "".join(f'<label class="qt"><input type="checkbox" value="{t}" data-key="{key}">'
                       f'<kbd>{key}</kbd> {t.replace("_"," ")}</label>' for key, t in tags)
    preasons = [("p", "premium_discount"), ("t", "too_far_from_ce"), ("j", "rr_misleading"),
                ("c", "insufficient_confirmation"), ("v", "fvg_location"), ("o", "other")]
    preason_html = "".join(f'<label class="qp"><input type="checkbox" value="{r}" data-key="{key}">'
                           f'<kbd>{key}</kbd> {r.replace("_"," ")}</label>' for key, r in preasons)
    prior_html = ""
    if prior:
        prior_html = (f'<div class="prior">prior label: <b>{"/".join(prior.get("decisions",[]))}</b>'
                      f' · conf {prior.get("confidence")} · {", ".join(prior.get("error_tags",[]))}'
                      f' <span class="mut">({prior.get("annotator","")})</span></div>')
    ver = '<span class="badge chg">changed vs prior engine</span>' if version_changed else ""
    return f"""
    <div class="qbar" data-i="{idx}" data-scene="{scene['scene_id']}">
      <div class="qtop">
        <span class="prog">reviewed {prog['reviewed']}/{prog['total']} · deferred {prog['deferred']}</span>
        <span class="sid">{scene['scene_id']}</span>{ver}
        <span class="score">info {scene.get('score','')}</span>
      </div>
      {prior_html}
      <div class="qrow"><span class="ql">decision</span>
        <button class="qb dir" data-v="LONG"><kbd>L</kbd> LONG</button>
        <button class="qb dir" data-v="SHORT"><kbd>S</kbd> SHORT</button>
        <button class="qb dir" data-v="NO_TRADE"><kbd>N</kbd> NO-TRADE</button>
        <span class="sep"></span>
        <button class="qb acc" data-v="ACCEPT"><kbd>A</kbd> accept engine</button>
        <button class="qb acc" data-v="REJECT"><kbd>R</kbd> reject engine</button>
      </div>
      <div class="qrow"><span class="ql">what's wrong</span><div class="qtags">{tag_html}</div></div>
      <div class="qrow"><span class="ql">confidence</span>
        <span class="conf">{''.join(f'<button class="qb cf" data-v="{i}"><kbd>{i}</kbd></button>' for i in range(1,6))}</span></div>
      <div class="qrow"><span class="ql">execute live?</span>
        <button class="qb ex" data-v="1"><kbd>y</kbd> YES</button>
        <button class="qb ex" data-v="0"><kbd>g</kbd> NO</button></div>
      <div class="qrow"><span class="ql">location 1-5</span>
        <span class="locq">{''.join(f'<button class="qb lq" data-v="{i}"><kbd>⇧{i}</kbd></button>' for i in range(1,6))}</span></div>
      <div class="qrow"><span class="ql">why pass</span><div class="qtags">{preason_html}</div></div>
      <div class="qrow"><span class="ql">note</span><textarea id="qnote" rows="1" placeholder="/ to focus"></textarea></div>
      <div class="qrow"><span class="ql"></span>
        <button class="qb go" id="qsave"><kbd>↵</kbd> save + next</button>
        <button class="qb" id="qskip"><kbd>.</kbd> skip</button>
        <button class="qb" id="qundo"><kbd>u</kbd> undo</button>
        <span id="qstatus" class="mut"></span></div>
    </div>
    <style>
      .qbar{{max-width:1000px;margin:0 auto;padding:16px 20px 70px;font-family:"IBM Plex Sans",system-ui,sans-serif;}}
      .qtop{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:10px;}}
      .prog{{font-weight:600;}} .sid{{font-family:"IBM Plex Mono",monospace;font-size:12px;color:var(--muted);}}
      .score{{margin-left:auto;font-family:"IBM Plex Mono",monospace;color:var(--muted);}}
      .badge.chg{{background:var(--warnbg);color:var(--warn);padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;}}
      .prior{{background:var(--surface2);border:1px solid var(--line);border-radius:8px;padding:8px 12px;font-size:13px;margin-bottom:10px;}}
      .qrow{{display:flex;gap:10px;align-items:center;margin:8px 0;flex-wrap:wrap;}}
      .ql{{flex:0 0 96px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);}}
      .qtags{{display:flex;gap:10px;flex-wrap:wrap;flex:1;}} .qt,.qp{{font-size:12px;color:var(--muted);display:flex;gap:4px;align-items:center;}}
      .qb{{font:inherit;font-size:13px;padding:6px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);cursor:pointer;}}
      .qb[aria-pressed="true"]{{background:var(--link);border-color:var(--link);color:#fff;}}
      .qb.go{{background:var(--ok);border-color:var(--ok);color:#fff;font-weight:600;}}
      kbd{{background:var(--mutbg);border-radius:4px;padding:1px 5px;font-size:11px;font-family:"IBM Plex Mono",monospace;}}
      .sep{{width:14px;}} #qnote{{flex:1;font:inherit;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);}}
    </style>
    <script>
    (function(){{
      var bar=document.querySelector('.qbar'), i=bar.dataset.i;
      var dir=null, acc=null, conf=null, exec_=null, locq=null, tags=new Set(), preasons=new Set(), note=document.getElementById('qnote');
      function press(sel,v,cur){{document.querySelectorAll(sel).forEach(function(b){{
        b.setAttribute('aria-pressed', b.dataset.v===String(v)?'true':'false');}}); return v;}}
      document.querySelectorAll('.dir').forEach(function(b){{b.onclick=function(){{dir=press('.dir',b.dataset.v);}};}});
      document.querySelectorAll('.acc').forEach(function(b){{b.onclick=function(){{acc=press('.acc',b.dataset.v);}};}});
      document.querySelectorAll('.cf').forEach(function(b){{b.onclick=function(){{conf=press('.cf',b.dataset.v);}};}});
      document.querySelectorAll('.ex').forEach(function(b){{b.onclick=function(){{exec_=press('.ex',b.dataset.v);}};}});
      document.querySelectorAll('.lq').forEach(function(b){{b.onclick=function(){{locq=press('.lq',b.dataset.v);}};}});
      document.querySelectorAll('.qt input').forEach(function(c){{c.onchange=function(){{c.checked?tags.add(c.value):tags.delete(c.value);}};}});
      document.querySelectorAll('.qp input').forEach(function(c){{c.onchange=function(){{c.checked?preasons.add(c.value):preasons.delete(c.value);}};}});
      function toggleTag(key){{var c=document.querySelector('.qt input[data-key="'+key+'"]'); if(c){{c.checked=!c.checked; c.checked?tags.add(c.value):tags.delete(c.value);}}}}
      function togglePR(key){{var c=document.querySelector('.qp input[data-key="'+key+'"]'); if(c){{c.checked=!c.checked; c.checked?preasons.add(c.value):preasons.delete(c.value);}}}}
      function post(url){{var decisions=[]; if(acc)decisions.push(acc); if(dir)decisions.push(dir);
        var body={{scene_id:bar.dataset.scene, i:i, decisions:decisions, error_tags:[].slice.call(tags),
          confidence:conf?conf/5:null, note:note.value,
          would_execute:(exec_===null?null:(exec_==='1')), location_quality:(locq?+locq:null),
          reason_for_pass:[].slice.call(preasons)}};
        var st=document.getElementById('qstatus');
        if(url==='/annotate'&&!decisions.length){{st.textContent='pick L/S/N or A/R'; return;}}
        st.textContent='saving…';
        fetch(url+'?i='+i,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(body)}})
          .then(function(r){{return r.json();}}).then(function(d){{
            if(d.next===null){{document.body.innerHTML='<h2 style=\\'text-align:center;margin-top:80px\\'>Queue complete ✓</h2>';}}
            else window.location='/scene?i='+d.next;}}).catch(function(e){{st.textContent='error: '+e;}});
      }}
      document.getElementById('qsave').onclick=function(){{post('/annotate');}};
      document.getElementById('qskip').onclick=function(){{post('/skip');}};
      document.getElementById('qundo').onclick=function(){{fetch('/undo',{{method:'POST'}}).then(function(){{location.reload();}});}};
      document.addEventListener('keydown',function(e){{
        if(document.activeElement===note){{ if(e.key==='Escape')note.blur(); return; }}
        var k=e.key.toLowerCase();
        if(k==='l')dir=press('.dir','LONG'); else if(k==='s')dir=press('.dir','SHORT');
        else if(k==='n')dir=press('.dir','NO_TRADE'); else if(k==='a')acc=press('.acc','ACCEPT');
        else if(k==='r')acc=press('.acc','REJECT'); else if('12345'.indexOf(k)>=0)conf=press('.cf',k);
        else if(k==='y')exec_=press('.ex','1'); else if(k==='g')exec_=press('.ex','0');
        else if('!@#$%'.indexOf(e.key)>=0)locq=press('.lq',String('!@#$%'.indexOf(e.key)+1));
        else if('mwkfdbi'.indexOf(k)>=0)toggleTag(k); else if('ptjcvo'.indexOf(k)>=0)togglePR(k);
        else if(k==='/'){{e.preventDefault();note.focus();}}
        else if(e.key==='Enter')post('/annotate'); else if(k==='.')post('/skip'); else if(k==='u'){{fetch('/undo',{{method:'POST'}}).then(function(){{location.reload();}});}}
      }});
    }})();
    </script>"""


def make_handler(session: Session, changed_scenes: set, round_name: str = "active_learning_queue"):
    ver = versioning.engine_version()

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj), "application/json")

        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            if u.path == "/":
                nxt = session.next_index()
                if nxt is None:
                    return self._send(200, "<h2>Queue complete ✓</h2>")
                return self._send(302, "") if False else self._redirect(f"/scene?i={nxt}")
            if u.path == "/next":
                nxt = session.next_index()
                return self._redirect("/" if nxt is None else f"/scene?i={nxt}")
            if u.path == "/scene":
                i = int(q.get("i", [0])[0]); scene = session.queue[i]
                ms, bar = build_state(scene)
                if ms is None:
                    return self._send(404, f"scene not reproducible: {scene['scene_id']}")
                sh = FR.shadow_report(ms, FR.FidelityRanker(), bar)      # untrained -> abstains
                html = render(ms, {"title": "Annotate", "symbol": scene["symbol"],
                                   "contract": scene["contract"], "time": scene["time"],
                                   "scene_id": scene["scene_id"], "annotate": False, "shadow": sh,
                                   "rr": (f"{ms.ranked_setups[0].item.rr:g}" if ms.ranked_setups else "")})
                html += _panel(scene, i, _prior(scene["scene_id"]), session.progress(),
                               scene["scene_id"] in changed_scenes)
                return self._send(200, html)
            self._send(404, "not found", "text/plain")

        def _redirect(self, loc):
            self.send_response(303); self.send_header("Location", loc); self.end_headers()

        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            i = int(q.get("i", [0])[0]) if "i" in q else None
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if u.path == "/annotate":
                scene = session.queue[i]
                try:
                    a = anno.make_annotation(candidate_id=body.get("candidate_id") or scene["scene_id"],
                                             decisions=body["decisions"], annotator=body.get("annotator", "gil"),
                                             error_tags=body.get("error_tags", []), note=body.get("note", ""),
                                             confidence=body.get("confidence"), candidate_time=scene["time"],
                                             would_execute=body.get("would_execute"),
                                             location_quality=body.get("location_quality"),
                                             reason_for_pass=body.get("reason_for_pass", []))
                except Exception as e:
                    return self._json(400, {"ok": False, "error": str(e)})
                a.update({"scene_id": scene["scene_id"], "symbol": scene["symbol"],
                          "contract": scene["contract"], "signal_tf": scene["signal_tf"],
                          "engine_version": ver, "info_score": scene.get("score"),
                          "provenance": {"round": round_name, "blinded": False}})
                Path(FIDELITY_PATH).parent.mkdir(parents=True, exist_ok=True)
                with open(FIDELITY_PATH, "a") as fh:
                    fh.write(json.dumps(a, default=str) + "\n")
                session.reviewed[scene["scene_id"]] = {"decisions": a["decisions"]}
                session.history.append(scene["scene_id"]); session.save()
                return self._json(200, {"ok": True, "next": session.next_index()})
            if u.path == "/skip":
                sid = session.queue[i]["scene_id"]
                if sid not in session.deferred:
                    session.deferred.append(sid)
                session.save()
                return self._json(200, {"ok": True, "next": session.next_index()})
            if u.path == "/undo":
                if session.history:
                    sid = session.history.pop()
                    session.reviewed.pop(sid, None)
                    lines = Path(FIDELITY_PATH).read_text().splitlines() if Path(FIDELITY_PATH).exists() else []
                    # drop the last annotation for that scene
                    for j in range(len(lines) - 1, -1, -1):
                        if json.loads(lines[j]).get("scene_id") == sid:
                            del lines[j]; break
                    Path(FIDELITY_PATH).write_text("\n".join(lines) + ("\n" if lines else ""))
                    session.save()
                return self._json(200, {"ok": True})
            self._json(404, {"ok": False})

        def log_message(self, *a):
            pass
    return H


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", required=True)
    ap.add_argument("--session", default="ict_live/research/datasets/annotation_session.json")
    ap.add_argument("--changed", default=None, help="optional versioning-diff JSON to flag changed scenes")
    ap.add_argument("--round", default="active_learning_queue", help="provenance round label")
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args(argv)
    queue = [json.loads(x) for x in Path(a.queue).read_text().splitlines() if x.strip()]
    session = Session(a.session, queue)
    changed = set()
    if a.changed and Path(a.changed).exists():
        changed = set(json.loads(Path(a.changed).read_text()).get("changed_scene_ids", []))
    handler = make_handler(session, changed, round_name=a.round)
    HTTPServer.allow_reuse_address = True
    httpd, port = None, a.port
    for cand in range(a.port, a.port + 10):          # skip a port already held by a running instance
        try:
            httpd = HTTPServer(("127.0.0.1", cand), handler)
            port = cand
            break
        except OSError:
            continue
    if httpd is None:
        print(f"[annotate] no free port in {a.port}..{a.port+9}. Stop the old server or pass --port.")
        return 1
    if port != a.port:
        print(f"[annotate] port {a.port} busy (another server running?) — using {port} instead.")
    p = session.progress()
    print(f"[annotate] queue {p['total']} scenes ({p['reviewed']} already reviewed) → "
          f"http://127.0.0.1:{port}  · fidelity → {FIDELITY_PATH}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[annotate] stopped (session saved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
