"""Render a MarketState's reasoning graph as a self-contained HTML inspector (the visual
microscope's reasoning view). Data-driven: works for any scene. Fidelity-first — every object's
why / rank / factors / state / dependencies / competitors / rejection is inspectable.
"""
from __future__ import annotations

import html

from ict_live.engine import reasoning
from ict_live.engine.pipeline import MarketState

_LAYERS = [("manipulation", "Manipulation — liquidity sweep"),
           ("displacement", "Displacement"),
           ("mss", "Market Structure Shift"),
           ("fvg", "Fair Value Gap"),
           ("setup", "Setup (entry / stop / target)")]

_STATE_CLASS = {"actionable": "ok", "rejected": "no", "active": "teal", "swept": "mut",
                "confirmed": "ok", "candidate": "warn", "potential": "mut",
                "unfilled": "teal", "touched": "warn", "mitigated": "mut",
                "bullish": "ok", "bearish": "no", "exhausted": "teal", "in_progress": "warn"}


def _e(x):
    return html.escape(str(x)) if x is not None else ""


def _pill(text, cls="mut"):
    return f'<span class="pill {cls}">{_e(text)}</span>' if text else ""


def _factors(d):
    if not d:
        return ""
    return " ".join(f'<span class="fac"><b>{_e(k)}</b>{_e(_fmt(v))}</span>' for k, v in d.items())


def _fmt(v):
    return f"{v:g}" if isinstance(v, float) else v


def _node_card(n, *, winner=False):
    st = n.get("state")
    pill = _pill(st, _STATE_CLASS.get(st, "mut"))
    rj = f'<div class="rej">rejected: {_e(n["reject_reason"])}</div>' if n.get("reject_reason") else ""
    if n.get("current_rank"):
        rank = (f'<span class="rk">current #{n["current_rank"]}{" (tie)" if n.get("current_tied") else ""}'
                f'<span class="gr2">· global #{n["rank"]}</span></span>')
    elif n.get("rank") is not None:
        note = "" if n.get("lifecycle") == "current" else '<span class="gr2">· not a current competitor</span>'
        rank = f'<span class="rk">#{n["rank"]}{" (tie)" if n.get("tied") else ""}{note}</span>'
    else:
        rank = ""
    return f"""<div class="card{' win' if winner else ''}">
      <div class="card-h"><span class="kind">{_e(n['kind'])}</span>{rank}
        <code class="id">{_e(n['id'])}</code>{pill}</div>
      <div class="why">{_e(n['why'])}</div>
      <div class="facs">{_factors(n.get('factors'))}</div>{rj}
    </div>"""


def render(ms: MarketState, scene: dict) -> str:
    g = reasoning.build_graph(ms)
    nodes = g["nodes"]
    rec = g["recommendation"]
    dec = rec["decision"]
    dec_cls = "ok" if dec == "LONG" else ("no" if dec == "SHORT" else "mut")

    chain_ids = reasoning.trace(g, rec["setup_id"]) if rec["setup_id"] else []
    chain_set = set(chain_ids)

    # winning spine, in methodology order
    spine = []
    order_kind = ["setup", "fvg", "mss", "displacement", "manipulation", "erl", "swing", "dealing_range"]
    for kind in order_kind:
        picks = [nid for nid in chain_ids if nodes[nid]["kind"] == kind]
        for nid in picks:
            spine.append(_node_card(nodes[nid], winner=True))
    spine_html = '<div class="spine-conn"></div>'.join(spine) if spine else \
        '<p class="empty">No actionable setup — see competing candidates and rejection reasons below.</p>'

    # competing candidates per layer — split CURRENT competitors from historical/active
    def _row(n, current=False):
        wcls = "win" if n["id"] in chain_set else ""
        st = _pill(n.get("state"), _STATE_CLASS.get(n.get("state"), "mut"))
        rej = _e(n["reject_reason"]) if n.get("reject_reason") else ""
        if current and n.get("current_rank"):
            # CURRENT view: rank/pairwise/factors recomputed over current competitors only
            rank_cell = (f'#{n["current_rank"]}{" t" if n.get("current_tied") else ""}'
                         f'<span class="gr">global #{n["rank"]}</span>')
            lost = _e(n.get("current_pairwise_reason", "")) or "TOP"
            facs = _factors(n.get("current_factors") or n.get("factors"))
        else:
            rank_cell = f'#{n["rank"]}{" t" if n.get("tied") else ""}'
            lost = _e(n["lost_to_prev"]) if n.get("rank", 1) and n["rank"] > 1 else "— top —"
            facs = _factors(n.get("factors"))
        return (f'<tr class="{wcls}"><td class="num">{rank_cell}</td>'
                f'<td><code>{_e(n["id"])}</code> {st}</td>'
                f'<td class="facs">{facs}</td>'
                f'<td class="lost">{lost}{f"<div class=\'rej\'>{rej}</div>" if rej else ""}</td></tr>')

    def _table(rows):
        return (f'<table><thead><tr><th>rank</th><th>object</th><th>ranking factors</th>'
                f'<th>why it ranked below / rejection</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')

    layer_blocks = []
    for kind, title in _LAYERS:
        comp = reasoning.competitors(g, kind)
        if not comp:
            continue
        cur = [n for n in comp if n.get("lifecycle") == "current"]
        hist = [n for n in comp if n.get("lifecycle") != "current"]
        cur_html = _table([_row(n, current=True) for n in cur]) if cur else '<p class="empty">no current competitor at this layer</p>'
        hist_html = (f'<details><summary>historical / superseded — {len(hist)} object'
                     f'{"s" if len(hist)!=1 else ""} (retained for audit; global rank kept, not ranked now)</summary>'
                     f'{_table([_row(n) for n in hist])}</details>') if hist else ""
        layer_blocks.append(f"""<section class="layer">
          <h3>{_e(title)} <span class="cnt">{len(cur)} current · {len(comp)} total</span></h3>
          {cur_html}{hist_html}
        </section>""")

    # full object inventory (foundation objects)
    inv = []
    for kind in ("dealing_range", "erl", "swing"):
        items = [n for n in nodes.values() if n["kind"] == kind]
        if not items:
            continue
        lis = "".join(f"""<li><code>{_e(n['id'])}</code> {_pill(n.get('state'), _STATE_CLASS.get(n.get('state'),'mut'))}
            <span class="dep">deps {len(n['depends_on'])} · children {len(n['children'])}</span>
            <div class="why">{_e(n['why'])}</div></li>""" for n in items)
        inv.append(f'<details><summary>{_e(kind)} <span class="cnt">{len(items)}</span></summary><ul class="inv">{lis}</ul></details>')

    lc = g.get("lifecycle", {}).get("counts", {})
    lc_line = ""
    if lc:
        lc_line = (f'<div class="lifecycle">lifecycle · setups: '
                   f'<b>{lc.get("current_setups",0)} current</b> · {lc.get("active_setups",0)} active · '
                   f'{lc.get("historical_setups",0)} historical &nbsp;|&nbsp; objects: '
                   f'{lc.get("current_objects",0)} current / {lc.get("historical_objects",0)} historical '
                   f'&nbsp;·&nbsp; only current compete for the recommendation</div>')

    # headline derives EXCLUSIVELY from the current winning setup (no stale/global metadata)
    win = ms.recommendation.setup
    win_rank = (ms.lifecycle or {}).get("current_ranking", {}).get(win.id, {}).get("current_rank") if win else None
    if win:
        win_rr = f"{win.rr:g}"
        tgt = f"{win.target:g}" if win.target is not None else "—"
        winner = (f'<code>{_e(win.id)}</code> · current #{win_rank} · {_e(win.direction).upper()} · '
                  f'entry {win.entry:g} / stop {win.stop:g} / target {tgt} · RR {win.rr:g}')
    else:
        win_rr, winner = "", "no current winning setup"

    # MVP execution-quality layer (deterministic, calibrated) — decision support ON TOP of structure
    from ict_live.engine import execution_quality as EQ
    ea = EQ.assess(ms)
    if ea.execution != "N/A":
        ecls = "ok" if ea.execution == "TRADE" else "no"
        reasons_html = ("".join(f"<li>{_e(r)}</li>" for r in ea.reasons)
                        if ea.reasons else "<li>no factor below the issue bar</li>")
        execrec = (f'<div class="execrec {ecls}">'
                   f'<div class="exh"><span class="el">Execution</span><b>{ea.execution}</b>'
                   f'<span class="ec">confidence {ea.confidence}</span>'
                   f'<span class="ec">weakest: {_e(ea.weakest_factor)}</span></div>'
                   f'<div class="exr"><span class="el">Reasons</span><ul>{reasons_html}</ul></div></div>')
    else:
        execrec = ""

    page = _PAGE.format(
        execrec=execrec,
        title=_e(scene.get("title", "Reasoning Graph")),
        lifecycle=lc_line,
        symbol=_e(scene.get("symbol", "")), contract=_e(scene.get("contract", "")),
        tf=_e(ms.tf), time=_e(scene.get("time", "")), nbars=ms.n_bars,
        dec=_e(dec), dec_cls=dec_cls,
        rr=_e(win_rr), reason=_e(rec["reason"]), winner=winner,
        counts=f"{len(ms.structural)} structural · {len(ms.active_erl)} active ERL · "
               f"{len(ms.ranked_sweeps)} sweeps · {len(ms.ranked_mss)} MSS · "
               f"{len(ms.ranked_fvgs)} FVG · {len(ms.ranked_setups)} setups",
        spine=spine_html, layers="".join(layer_blocks), inventory="".join(inv),
        shadow=_shadow_block(scene.get("shadow")))
    if not scene.get("annotate", True):
        return page                       # queue app supplies its own keyboard-driven panel
    cand_id = scene.get("candidate_id") or rec["setup_id"] or scene.get("scene_id", "scene")
    return page + _annotation_panel(scene.get("scene_id", "scene"), cand_id,
                                    scene.get("annotate_endpoint", "/annotate"))


def _shadow_block(shadow) -> str:
    """Deterministic vs learning-layer (shadow) comparison. Shadow NEVER changes the recommendation."""
    if not shadow:
        return ""
    fs = shadow.get("fidelity_shadow", {})
    if fs.get("abstain", True):
        learn = f'<span class="pill mut">shadow: {_e(fs.get("status","untrained"))}</span>'
    else:
        top = max(fs.get("per_candidate", []), key=lambda c: (c["p"] or 0), default=None)
        learn = (f'<span class="pill teal">P(NO-TRADE) {fs.get("p_no_trade")}</span> '
                 f'<span class="pill ok">top {_e(top["candidate_id"]) if top else "-"} '
                 f'p={top["p"] if top else "-"}</span>')
    return (f'<div class="shadow"><span class="alab">Deterministic</span>'
            f'<span class="pill">{_e(shadow.get("deterministic"))}</span>'
            f'<span class="alab" style="margin-left:18px">Learning (shadow)</span>{learn}'
            f'<span class="snote">learning layer never changes the deterministic recommendation</span></div>'
            '<style>.shadow{display:flex;gap:10px;align-items:center;flex-wrap:wrap;'
            'background:var(--surface2);border:1px solid var(--line);border-radius:10px;'
            'padding:12px 16px;margin:6px 0 22px;} .shadow .pill{background:var(--mutbg);color:var(--ink);}'
            '.snote{color:var(--muted);font-size:11px;flex-basis:100%;}</style>')


_TAGS = ["wrong_manipulation", "wrong_sweep", "wrong_mss", "wrong_fvg",
         "wrong_dealing_range_context", "bad_location", "insufficient_confirmation", "other"]


def _annotation_panel(scene_id: str, candidate_id: str, endpoint: str) -> str:
    decisions = "".join(f'<button type="button" class="atog" data-v="{d}">{d}</button>'
                        for d in ["ACCEPT", "REJECT", "LONG", "SHORT", "NO_TRADE"])
    tags = "".join(f'<label class="atag"><input type="checkbox" value="{t}">{t.replace("_"," ")}</label>'
                   for t in _TAGS)
    # HTML carries the ids via data-* attributes; the JS below uses real braces (not .format-ed)
    head = (f'<div class="anno" data-scene="{_e(scene_id)}" data-cand="{_e(candidate_id)}" '
            f'data-endpoint="{_e(endpoint)}">')
    return head + """
      <h2 style="margin-top:0">Fidelity annotation</h2>
      <p class="ahint">Mark what an expert ICT trader would judge here. Saved to the human-fidelity
        dataset (kept separate from market outcomes) and used to train the fidelity model.</p>
      <div class="arow"><span class="alab">Decision</span><div class="adec">""" + decisions + """</div></div>
      <div class="arow"><span class="alab">What's wrong</span><div class="atags">""" + tags + """</div></div>
      <div class="arow"><span class="alab">Confidence</span>
        <input id="aconf" type="range" min="0" max="1" step="0.05" value="0.7">
        <output id="aconfv">0.70</output></div>
      <div class="arow"><span class="alab">Note</span>
        <textarea id="anote" rows="2" placeholder="free text (required if 'other')"></textarea></div>
      <div class="arow"><span class="alab"></span>
        <button id="asave" type="button">Save annotation</button>
        <span id="astatus" class="astatus"></span></div>
      <textarea id="afallback" class="afallback" readonly hidden></textarea>
    </div>
    <style>
      .anno{max-width:1000px;margin:0 auto;padding:24px 20px 90px;}
      .anno h2{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
        border-bottom:1px solid var(--line);padding-bottom:8px;}
      .ahint{color:var(--muted);font-size:12.5px;max-width:60ch;}
      .arow{display:flex;gap:14px;align-items:flex-start;margin:12px 0;}
      .alab{flex:0 0 96px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
        padding-top:7px;}
      .adec,.atags{display:flex;flex-wrap:wrap;gap:8px;flex:1;}
      .atog{font:inherit;font-size:12px;padding:6px 12px;border:1px solid var(--line);border-radius:999px;
        background:var(--surface);color:var(--ink);cursor:pointer;}
      .atog[aria-pressed="true"]{background:var(--link);border-color:var(--link);color:#fff;}
      .atag{font-size:12px;color:var(--muted);display:flex;gap:5px;align-items:center;}
      #anote,.afallback{flex:1;font:inherit;font-size:13px;padding:8px;border:1px solid var(--line);
        border-radius:8px;background:var(--surface);color:var(--ink);font-family:"IBM Plex Mono",monospace;}
      #asave{font:inherit;font-weight:600;padding:8px 18px;border:0;border-radius:8px;background:var(--link);
        color:#fff;cursor:pointer;}
      .astatus{align-self:center;font-size:12.5px;color:var(--muted);}
      output{align-self:center;font-family:"IBM Plex Mono",monospace;}
    </style>
    <script>
    (function(){
      var root=document.querySelector('.anno'), picked=new Set();
      root.querySelectorAll('.atog').forEach(function(b){
        b.setAttribute('aria-pressed','false');
        b.addEventListener('click',function(){
          var on=b.getAttribute('aria-pressed')==='true';
          b.setAttribute('aria-pressed', on?'false':'true');
          if(on)picked.delete(b.dataset.v); else picked.add(b.dataset.v);
        });
      });
      var conf=document.getElementById('aconf'), confv=document.getElementById('aconfv');
      conf.addEventListener('input',function(){confv.textContent=(+conf.value).toFixed(2);});
      document.getElementById('asave').addEventListener('click',function(){
        var tags=[].slice.call(root.querySelectorAll('.atags input:checked')).map(function(i){return i.value;});
        var payload={type:'human_fidelity', scene_id:root.dataset.scene, candidate_id:root.dataset.cand,
          decisions:[].slice.call(picked), error_tags:tags,
          confidence:+conf.value, note:document.getElementById('anote').value};
        var st=document.getElementById('astatus');
        if(!payload.decisions.length){st.textContent='pick at least one decision'; return;}
        st.textContent='saving…';
        fetch(root.dataset.endpoint,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify(payload)})
          .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json(); })
          .then(function(){ st.textContent='saved to fidelity dataset ✓'; })
          .catch(function(){
            st.textContent='no local server — copy this JSON into the dataset:';
            var fb=document.getElementById('afallback'); fb.hidden=false;
            fb.value=JSON.stringify(payload); fb.rows=3;
          });
      });
    })();
    </script>"""


_PAGE = """<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
:root {{
  --bg:#eef1f4; --surface:#ffffff; --surface2:#f5f8fb; --ink:#0f1720; --muted:#5b6b7a;
  --line:#d5dee7; --link:#0e7490; --ok:#15803d; --no:#b91c1c; --warn:#b45309; --teal:#0891b2;
  --okbg:#dcfce7; --nobg:#fee2e2; --warnbg:#fef3c7; --tealbg:#cffafe; --mutbg:#e8edf2;
}}
:root:not([data-theme="light"]) {{ }}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#0b1015; --surface:#131c25; --surface2:#0f1720; --ink:#e7eef5; --muted:#8ea0b0;
    --line:#233140; --link:#22d3ee; --ok:#4ade80; --no:#f87171; --warn:#fbbf24; --teal:#38bdf8;
    --okbg:#0f2a1a; --nobg:#2a1315; --warnbg:#2a2110; --tealbg:#0c2530; --mutbg:#1a2430;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0b1015; --surface:#131c25; --surface2:#0f1720; --ink:#e7eef5; --muted:#8ea0b0;
  --line:#233140; --link:#22d3ee; --ok:#4ade80; --no:#f87171; --warn:#fbbf24; --teal:#38bdf8;
  --okbg:#0f2a1a; --nobg:#2a1315; --warnbg:#2a2110; --tealbg:#0c2530; --mutbg:#1a2430;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans",system-ui,sans-serif; line-height:1.5;
  font-variant-numeric:tabular-nums; }}
.wrap {{ max-width:1000px; margin:0 auto; padding:32px 20px 80px; }}
code, .facs, .num {{ font-family:"IBM Plex Mono",ui-monospace,monospace; }}
h1 {{ font-size:15px; font-weight:600; letter-spacing:.14em; text-transform:uppercase;
  color:var(--muted); margin:0 0 4px; }}
.scene {{ font-family:"IBM Plex Mono",monospace; color:var(--muted); font-size:13px; margin-bottom:20px; }}
.banner {{ background:var(--surface); border:1px solid var(--line); border-radius:14px;
  padding:22px 24px; display:flex; gap:22px; align-items:center; flex-wrap:wrap;
  border-left:6px solid var(--dec-accent); }}
.banner.ok {{ --dec-accent:var(--ok); }} .banner.no {{ --dec-accent:var(--no); }}
.banner.mut {{ --dec-accent:var(--muted); }}
.decision {{ font-size:40px; font-weight:700; letter-spacing:-.02em; color:var(--dec-accent);
  line-height:1; }}
.rr {{ font-family:"IBM Plex Mono",monospace; font-size:22px; font-weight:600; }}
.rr small {{ color:var(--muted); font-size:12px; font-weight:500; display:block; letter-spacing:.1em;
  text-transform:uppercase; }}
.rec-reason {{ flex:1 1 320px; color:var(--muted); font-size:13.5px; }}
.winner {{ color:var(--ink); font-weight:600; font-size:13px; margin-bottom:4px;
  font-family:"IBM Plex Mono",monospace; }}
.counts {{ margin:10px 0 8px; color:var(--muted); font-size:12.5px; font-family:"IBM Plex Mono",monospace; }}
.lifecycle {{ margin:0 0 22px; padding:8px 12px; font-size:12.5px; border-radius:8px;
  background:var(--tealbg); color:var(--ink); border:1px solid var(--line); }}
.lifecycle b {{ color:var(--ok); }}
.execrec {{ margin:0 0 22px; padding:12px 16px; border-radius:10px; border:1px solid var(--line);
  background:var(--surface2); border-left:6px solid var(--er-accent); }}
.execrec.ok {{ --er-accent:var(--ok); }} .execrec.no {{ --er-accent:var(--no); }}
.execrec .exh {{ display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }}
.execrec .el {{ font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); }}
.execrec b {{ font-size:18px; color:var(--er-accent); }}
.execrec .ec {{ font-family:"IBM Plex Mono",monospace; font-size:12.5px; color:var(--muted); }}
.execrec .exr {{ display:flex; gap:12px; margin-top:8px; }}
.execrec .exr ul {{ margin:0; padding-left:18px; font-size:12.5px; color:var(--ink); }}
h2 {{ font-size:12px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted);
  margin:34px 0 14px; padding-bottom:8px; border-bottom:1px solid var(--line); }}
.spine {{ display:flex; flex-direction:column; align-items:stretch; gap:0; }}
.spine-conn {{ width:2px; height:18px; background:var(--line); margin:0 auto; }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
.card.win {{ border-color:var(--link); box-shadow:0 0 0 1px var(--link) inset; }}
.card-h {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:6px; }}
.kind {{ font-size:11px; letter-spacing:.12em; text-transform:uppercase; font-weight:600; color:var(--link); }}
.rk {{ font-family:"IBM Plex Mono",monospace; font-weight:600; font-size:13px; }}
.rk .gr2 {{ font-weight:400; font-size:11px; color:var(--muted); margin-left:6px; }}
.id {{ font-size:12px; color:var(--muted); }}
.why {{ font-size:13px; color:var(--ink); }}
.facs {{ display:flex; flex-wrap:wrap; gap:6px 12px; margin-top:8px; }}
.fac {{ font-size:11.5px; color:var(--muted); }} .fac b {{ color:var(--ink); font-weight:600; margin-right:4px; }}
.rej {{ margin-top:6px; font-size:12px; color:var(--no); }}
.pill {{ font-size:10.5px; font-weight:600; padding:2px 8px; border-radius:999px; letter-spacing:.04em;
  text-transform:uppercase; }}
.pill.ok {{ background:var(--okbg); color:var(--ok); }} .pill.no {{ background:var(--nobg); color:var(--no); }}
.pill.warn {{ background:var(--warnbg); color:var(--warn); }} .pill.teal {{ background:var(--tealbg); color:var(--teal); }}
.pill.mut {{ background:var(--mutbg); color:var(--muted); }}
.layer {{ margin-bottom:26px; }}
.layer h3 {{ font-size:14px; font-weight:600; margin:0 0 10px; display:flex; gap:10px; align-items:baseline; }}
.cnt {{ font-size:11px; color:var(--muted); font-weight:500; font-family:"IBM Plex Mono",monospace; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
.layer {{ overflow-x:auto; }}
th {{ text-align:left; font-size:10.5px; letter-spacing:.1em; text-transform:uppercase; color:var(--muted);
  font-weight:600; padding:6px 10px; border-bottom:1px solid var(--line); }}
td {{ padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
tr.win td {{ background:color-mix(in srgb, var(--link) 8%, transparent); }}
td.num {{ font-family:"IBM Plex Mono",monospace; font-weight:600; white-space:nowrap; }}
td.num .gr {{ display:block; font-weight:400; font-size:10px; color:var(--muted); }}
td.lost {{ color:var(--muted); font-size:12px; }}
details {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:10px 14px; margin-bottom:10px; }}
summary {{ cursor:pointer; font-weight:600; font-size:13px; }}
ul.inv {{ list-style:none; padding:0; margin:12px 0 0; display:flex; flex-direction:column; gap:12px; }}
ul.inv .dep {{ font-size:11px; color:var(--muted); margin-left:8px; }}
.empty {{ color:var(--muted); background:var(--surface2); border:1px dashed var(--line);
  border-radius:12px; padding:18px; text-align:center; }}
</style>
<div class="wrap">
  <h1>ICT Engine · Reasoning Graph</h1>
  <div class="scene">{symbol} · {contract} · {tf} · {time} · window {nbars} bars</div>
  <div class="banner {dec_cls}">
    <div class="decision">{dec}</div>
    <div class="rr">{rr}<small>reward : risk</small></div>
    <div class="rec-reason"><div class="winner">{winner}</div>{reason}</div>
  </div>
  {execrec}
  <div class="counts">{counts}</div>
  {lifecycle}
  {shadow}
  <h2>Winning reasoning spine</h2>
  <div class="spine">{spine}</div>

  <h2>Competing candidates — ranked, not filtered</h2>
  {layers}

  <h2>Foundation objects</h2>
  {inventory}
</div>"""
