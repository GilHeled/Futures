"""The scenario-analysis chart page (served at /scenario).

The CHART is a clean HRLR/LRLR liquidity-run view with a timeframe selector (4H/1H/30m/15m/5m/1m),
fed by the read-only /candles endpoint. The side rail keeps the engine's read-only analysis
(nested P/D, FVG state, liquidity draws, absent-gaps, conditional scenarios). Pure presentation; it
never changes engine state."""

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scenario Analysis</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap">
<style>
:root{--bg:#0d1117;--panel:#151a23;--panel2:#1b222d;--line:#232b38;--line2:#2c3644;--text:#d1d4dc;--muted:#868b98;--dim:#5b636f;
--green:#26a69a;--green-soft:rgba(38,166,154,.14);--red:#ef5350;--red-soft:rgba(239,83,80,.13);--orange:#e8983a;--amber:#f0b429;--amber-soft:rgba(240,180,41,.12);
--lrlr:#2962ff;--mono:'JetBrains Mono',ui-monospace,Menlo,monospace;--sans:'Inter',system-ui,sans-serif;}
*{box-sizing:border-box}body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;line-height:1.5;margin:0}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}.wrap{max-width:1360px;margin:0 auto;padding:16px 18px 44px}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding-bottom:12px;border-bottom:1px solid var(--line);margin-bottom:14px}
.sym{font-weight:700;font-size:17px}.sub{color:var(--muted);font-size:12px}.price{font-family:var(--mono);font-weight:600;font-size:16px}
.flag{margin-left:auto;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--amber);border:1px solid rgba(240,180,41,.4);background:var(--amber-soft);padding:4px 9px;border-radius:4px;font-weight:600}
.grid{display:grid;grid-template-columns:1fr 340px;gap:14px}@media(max-width:960px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:7px}
.card h3{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:600;margin:0;padding:11px 13px 9px;border-bottom:1px solid var(--line)}
.chhead{display:flex;align-items:center;gap:10px;padding:9px 13px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.chhead .ttl{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:600}
.tfbar{display:flex;gap:4px;margin-left:auto}
.tfbtn{font-family:var(--mono);font-size:11px;color:var(--muted);background:var(--panel2);border:1px solid var(--line2);border-radius:4px;padding:3px 9px;cursor:pointer}
.tfbtn:hover{color:var(--text);border-color:var(--dim)}
.tfbtn.on{color:#fff;background:var(--lrlr);border-color:var(--lrlr)}
.chart{position:relative;height:600px;margin:6px 8px 8px}
.lvl{position:absolute;left:0;right:70px;height:0;opacity:.9}
.lvl .lab{position:absolute;top:-9px;right:1px;font-family:var(--mono);font-size:9px;padding:0 2px;background:var(--bg);border-radius:2px}
.axis{position:absolute;right:-70px;top:-8px;width:66px;text-align:right;font-family:var(--mono);font-size:10px;color:var(--dim)}
.now{position:absolute;left:0;right:70px;height:0;border-top:1px dashed var(--red)}
.now .nt{position:absolute;right:-70px;top:-9px;width:66px;text-align:right;font-family:var(--mono);font-size:10.5px;color:#fff;background:var(--red);border-radius:3px;padding:1px 3px}
.caption{color:var(--dim);font-size:10.5px;padding:0 13px 10px;font-style:italic}
.legend{display:flex;gap:16px;flex-wrap:wrap;padding:9px 13px;border-top:1px solid var(--line);color:var(--muted);font-size:10.5px}
.legend span{display:inline-flex;align-items:center;gap:5px}.sw{width:16px;height:9px;border-radius:2px;display:inline-block}
.rail{display:flex;flex-direction:column;gap:14px}.ctx{padding:11px 13px;display:flex;flex-direction:column;gap:11px}
.rng{display:grid;grid-template-columns:auto 1fr auto;gap:2px 10px;align-items:center;padding:9px 10px;border-radius:5px;background:var(--panel2);border:1px solid var(--line)}
.rng .role{font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}.rng .tf{font-family:var(--mono);font-size:11px;color:var(--dim);text-align:right}
.pd{grid-column:1/-1;display:flex;align-items:center;gap:8px;margin-top:3px}.chip{font-size:10.5px;font-weight:600;padding:2px 8px;border-radius:4px}
.chip.prem{color:var(--red);background:var(--red-soft);border:1px solid rgba(239,83,80,.35)}.chip.disc{color:var(--green);background:var(--green-soft);border:1px solid rgba(38,166,154,.35)}
.rng .meta{grid-column:1/-1;font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:12px}td,th{padding:6px 13px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:500;font-size:10.5px;letter-spacing:.05em;text-transform:uppercase}tr:last-child td{border-bottom:none}
td.n{font-family:var(--mono);text-align:right}.tf-badge{font-family:var(--mono);font-size:10px;color:var(--dim)}.k-buy{color:var(--green)}.k-sell{color:var(--red)}
.pill{font-size:10px;padding:1px 6px;border-radius:3px;border:1px solid var(--line2);color:var(--muted)}
.st-FRESH{color:var(--text)}.st-TOUCHED{color:var(--amber)}.st-RESPECTED{color:var(--green)}.st-FAILED{color:var(--red)}.st-CLOSED{color:var(--dim)}
.scn{border-left:3px solid;padding:11px 13px}.scn.A{border-color:var(--green)}.scn.B{border-color:var(--red)}.scn.C{border-color:var(--dim)}
.scn .stt{display:flex;align-items:center;gap:8px;margin-bottom:7px}.scn .name{font-weight:600}.state{margin-left:auto;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:4px;border:1px solid var(--line2);color:var(--muted)}
.state.act{color:var(--green);border-color:rgba(38,166,154,.4);background:var(--green-soft)}.state.wait{color:var(--amber);border-color:rgba(240,180,41,.4);background:var(--amber-soft)}.state.inv{color:var(--red);border-color:rgba(239,83,80,.4);background:var(--red-soft)}
.seq{display:grid;grid-template-columns:auto 1fr;gap:3px 9px;font-size:11.5px;margin-top:4px}.seq b{color:var(--muted);font-weight:500;font-size:10px;letter-spacing:.04em;text-transform:uppercase;padding-top:1px}
.geo{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:7px;padding-top:7px;border-top:1px dashed var(--line2)}
.err{padding:20px;color:var(--amber)}
</style></head><body><div class="wrap" id="root"><div class="err">Loading /report…</div></div>
<script>
const F=(x,d)=>x==null?'—':Number(x).toLocaleString(undefined,{minimumFractionDigits:d||0,maximumFractionDigits:d||0});
const F2=x=>F(x,2), el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const TFS=['4H','1H','30m','15m','5m','1m'];
let CUR={sym:null, tf:'15m', data:null, showRuns:true, lastDay:true};

async function load(){
  let rep={symbols:{}}, list=[];
  try{ rep=await (await fetch('/report',{cache:'no-store'})).json(); }catch(e){}
  try{ list=((await (await fetch('/symbols',{cache:'no-store'})).json()).symbols)||[]; }catch(e){}
  CUR.report=rep.symbols||{}; CUR.symsList=list;
  const names=list.length?list.map(x=>x.sym):Object.keys(CUR.report);
  if(!names.length){ document.getElementById('root').innerHTML='<div class="err">no symbols available yet</div>'; return; }
  const q=new URLSearchParams(location.search).get('sym');
  if(!(CUR.sym&&names.includes(CUR.sym))){
    CUR.sym = (q&&names.includes(q)) ? q
      : (names.find(k=>/MNQZ2026/i.test(k)) || names.find(k=>/MNQ/i.test(k)) || names[0]);
  }
  renderShell();
  loadCandles();
}

function renderShell(){
  const root=document.getElementById('root'); root.innerHTML='';
  const sym=CUR.sym;
  const A=(CUR.report&&CUR.report[sym]&&CUR.report[sym].scenario_analysis)||null;
  const P=A?A.price:null, par=A?A.ranges.parent:null, intr=A?A.ranges.internal:null;
  const top=el('div','top');
  top.appendChild(el('span','sym',(sym.split(':').pop())));
  // symbol selector
  const sel=el('select');sel.style.cssText='background:var(--panel2);color:var(--text);border:1px solid var(--line2);border-radius:4px;font-family:var(--mono);font-size:12px;padding:3px 7px;cursor:pointer';
  (CUR.symsList||[]).forEach(o=>{const op=el('option');op.value=o.sym;op.textContent=o.sym.split(':').pop();if(o.sym===sym)op.selected=true;sel.appendChild(op);});
  sel.onchange=()=>{CUR.sym=sel.value;CUR.data=null;renderShell();loadCandles();};
  top.appendChild(sel);
  if(P!=null)top.appendChild(el('span','price',F2(P)));
  top.appendChild(el('span','sub','HRLR / LRLR liquidity runs'));
  top.appendChild(el('span','flag','read-only · scenario ≠ live order'));
  root.appendChild(top);

  const grid=el('div','grid');
  // chart card with TF selector
  const cc=el('div','card');
  const hd=el('div','chhead');
  hd.appendChild(el('span','ttl','Chart · HRLR / LRLR only'));
  const tfbar=el('div','tfbar');
  TFS.forEach(tf=>{const b=el('button','tfbtn'+(tf===CUR.tf?' on':''),tf);b.dataset.tf=tf;
    b.onclick=()=>{CUR.tf=tf;[...tfbar.children].forEach(x=>x.classList.toggle('on',x.dataset.tf===tf));loadCandles();};
    tfbar.appendChild(b);});
  hd.appendChild(tfbar);
  const ctrls=el('div','tfbar');ctrls.style.marginLeft='0';
  const tbtn=(key,lab)=>{const b=el('button','tfbtn'+(CUR[key]?' on':''),lab);
    b.onclick=()=>{CUR[key]=!CUR[key];b.classList.toggle('on',CUR[key]);drawChart();};return b;};
  ctrls.appendChild(tbtn('showRuns','HRLR/LRLR'));ctrls.appendChild(tbtn('lastDay','Last day'));
  hd.appendChild(ctrls); cc.appendChild(hd);
  const chart=el('div','chart');chart.id='chart';chart.innerHTML='<div class="err">Loading candles…</div>';cc.appendChild(chart);
  cc.appendChild(el('div','caption','Standalone HRLR/LRLR indicator (pivots 5/2, EQ tolerance 10 ticks). LRLR = EQH/EQL · HRLR = sweep. Mitigated runs stop at their mitigation bar and dim.'));
  const lg=el('div','legend');
  lg.innerHTML='<span><i class="sw" style="border-top:2px solid var(--lrlr);height:0"></i>LRLR (EQH/EQL)</span>'+
   '<span><i class="sw" style="border-top:2px dotted var(--red);height:0"></i>HRLR↑ swept high (bearish)</span>'+
   '<span><i class="sw" style="border-top:2px dotted var(--green);height:0"></i>HRLR↓ swept low (bullish)</span>'+
   '<span style="color:var(--dim)">dim = mitigated</span>';
  cc.appendChild(lg);
  grid.appendChild(cc);

  // rail (engine read-only analysis) — only when the engine has ingested this symbol
  const rail=el('div','rail');
  if(A){
  const ctxCard=el('div','card');ctxCard.appendChild(el('h3',null,'Market context — nested P/D'));const ctx=el('div','ctx');
  [['Parent range',par],['Internal range',intr]].forEach(([role,r])=>{const rw=el('div','rng');
    if(!r.available){rw.innerHTML='<span class="role">'+role+'</span><span></span><span class="tf">'+(r.tf||'?')+'</span><div class="pd"><span class="pill">unavailable</span></div>';ctx.appendChild(rw);return;}
    const cls=r.price_class==='PREMIUM'?'prem':'disc';
    rw.innerHTML='<span class="role">'+role+'</span><span></span><span class="tf">'+r.tf+' · engine</span>'+
     '<div class="pd"><span class="chip '+cls+'">'+r.price_class+'</span><span class="num" style="color:var(--muted);font-size:11px">EQ '+F2(r.eq)+'</span></div>'+
     '<div class="meta">0 · '+F2(r.fib['0'])+'   0.5 · '+F2(r.fib['0.5'])+'   1 · '+F2(r.fib['1'])+'</div>';
    ctx.appendChild(rw);});
  ctxCard.appendChild(ctx);rail.appendChild(ctxCard);
  // liquidity runs panel (from report; the chart shows the full detail)
  const LRUN=A.liquidity_runs||{};
  const rc=el('div','card');rc.appendChild(el('h3',null,'Liquidity runs · HRLR / LRLR ('+(LRUN.tf||'')+' engine)'));
  rc.innerHTML+='<div style="padding:10px 13px;font-size:12px">'+
    '<span class="num" style="color:var(--lrlr)">LRLR '+(LRUN.lrlr||0)+'</span> <span style="color:var(--muted)">EQH/EQL</span> · '+
    '<span class="num" style="color:var(--red)">HRLR '+(LRUN.hrlr||0)+'</span> <span style="color:var(--muted)">sweeps</span> · '+
    '<span class="num" style="color:var(--dim)">std '+(LRUN.standard||0)+'</span>'+
    '<div style="margin-top:7px;color:var(--dim);font-size:10.5px;line-height:1.5">LRLR = the EQH/EQL the course leaves un-toleranced — using the indicator’s own 10-tick parameter, not an engine-invented one. The chart above recomputes per selected timeframe. Read-only; not wired to trades.</div></div>';
  rail.appendChild(rc);
  // FVG state
  const fc=el('div','card');fc.appendChild(el('h3',null,'Fair-value gaps · state'));const ft=el('table');
  ft.innerHTML='<tr><th>TF</th><th>Dir</th><th>Zone</th><th>State</th></tr>';
  if(!A.fvgs.length)ft.innerHTML+='<tr><td colspan="4" style="color:var(--muted)">no eligible FVGs</td></tr>';
  A.fvgs.forEach(f=>{ft.innerHTML+='<tr><td class="tf-badge">'+f.tf+'</td><td class="'+(f.direction==='bullish'?'k-buy':'k-sell')+'">'+(f.direction==='bullish'?'bull':'bear')+'</td><td class="n">'+F2(f.bottom)+' – '+F2(f.top)+'</td><td class="st-'+f.state+'">'+f.state+'</td></tr>';});
  fc.appendChild(ft);rail.appendChild(fc);
  // liquidity draws
  const lc=el('div','card');lc.appendChild(el('h3',null,'Liquidity draws'));const lt=el('table');lt.innerHTML='<tr><th>Draw</th><th>Px</th><th>TF</th><th>Class</th></tr>';
  [['BSL nearest','nearest_bsl'],['BSL next','next_bsl'],['SSL nearest','nearest_ssl'],['SSL next','next_ssl']].forEach(([lab,k])=>{const o=A.liquidity[k];if(!o)return;lt.innerHTML+='<tr><td class="'+(lab[0]==='B'?'k-buy':'k-sell')+'">'+lab+'</td><td class="n">'+F2(o.price)+'</td><td class="tf-badge">'+o.tf+'</td><td><span class="pill">'+o.class+'</span></td></tr>';});
  lc.appendChild(lt);rail.appendChild(lc);
  } else {
    const wc=el('div','card');wc.appendChild(el('h3',null,'Engine analysis'));
    wc.innerHTML+='<div style="padding:12px 13px;color:var(--muted);font-size:12px;line-height:1.6">The read-only <b style="color:var(--text)">HRLR/LRLR chart</b> above is live for <b class="num" style="color:var(--text)">'+(sym.split(':').pop())+'</b>.<br>The engine’s nested P/D · FVG · scenario analysis for this symbol is still warming up (each symbol re-ingests sequentially, ~90s after a restart) and will appear here automatically.</div>';
    rail.appendChild(wc);
  }
  grid.appendChild(rail); root.appendChild(grid);

  // scenarios + narrative (read-only engine analysis)
  if(A){
  const scc=el('div','card');scc.style.marginTop='14px';scc.appendChild(el('h3',null,'Conditional paths — where → interaction → confirmation → direction → draw'));
  A.scenarios.forEach((s,i)=>{if(i>0){const hr=el('div');hr.style.cssText='height:1px;background:var(--line)';scc.appendChild(hr);}
    const active=/ACTIVE/.test(s.state),wait=/WAIT|AWAITING/.test(s.state),inv=/INVALID/.test(s.state);
    const stc=active?'act':inv?'inv':wait?'wait':'';const box=el('div','scn '+s.id);
    let seq=s.id!=='C'?('<div class="seq"><b>Where</b><span>'+(s.where||'')+'</span><b>Confirm</b><span>'+(s.confirmation||'')+'</span><b>Direction</b><span>'+(s.direction||'')+'</span><b>Draw</b><span>'+(s.draw?('<b class="num">'+F2(s.draw.price)+'</b> ('+s.draw.tf+' '+s.draw.class+')'):'—')+(s.next?' → '+F2(s.next.price):'')+'</span></div>'):('<div class="seq"><b>Where</b><span>'+s.why+'</span><b>Action</b><span>WAIT — no path armed</span></div>');
    let geo=s.geometry?('<div class="geo">engine live: entry '+F2(s.geometry.entry)+' · stop '+F2(s.geometry.stop)+' · target '+F2(s.geometry.target)+' · <b style="color:var(--text)">'+s.geometry.rr+'R</b></div>'):(s.id!=='C'?'<div class="geo">geometry deferred until confirmation · actionable only if ≥2R</div>':'');
    box.innerHTML='<div class="stt"><span class="name" style="color:'+(s.id==='A'?'var(--green)':s.id==='B'?'var(--red)':'var(--muted)')+'">Scenario '+s.id+' · '+s.path+' path</span><span class="state '+stc+'">'+s.state+'</span></div>'+seq+geo;
    scc.appendChild(box);});
  root.appendChild(scc);
  }
}

async function loadCandles(){
  const chart=document.getElementById('chart'); if(!chart)return;
  let data; try{ data=await (await fetch('/candles?sym='+encodeURIComponent(CUR.sym)+'&tf='+encodeURIComponent(CUR.tf)+'&n=200',{cache:'no-store'})).json(); }
  catch(e){ chart.innerHTML='<div class="err">/candles error: '+e+'</div>'; return; }
  CUR.data=data; drawChart();
}

function drawChart(){
  const chart=document.getElementById('chart'); if(!chart)return; chart.innerHTML='';
  const data=CUR.data; if(!data){return;}
  if(data.error){ chart.innerHTML='<div class="err">'+data.error+'</div>'; return; }
  let bars=data.bars||[];
  if(!bars.length){ chart.innerHTML='<div class="err">no '+data.tf+' candles available for this symbol yet</div>'; return; }
  let pools=(data.liquidity_runs&&data.liquidity_runs.pools)||[];
  // "Last day" view: keep the most recent calendar session; remap run indices to the slice
  // (a level carried in from before is clamped to the left edge; one mitigated before the day is dropped).
  let offset=0;
  if(CUR.lastDay){
    const lastDate=bars[bars.length-1].t.slice(0,10);
    const fi=bars.findIndex(b=>b.t.slice(0,10)===lastDate);
    if(fi>0){offset=fi; bars=bars.slice(fi);}
  }
  pools=pools.reduce((acc,r)=>{if(r.pivot_index==null)return acc;
    let pi=r.pivot_index-offset, mi=(r.mitigation_index!=null)?r.mitigation_index-offset:null;
    if(r.mitigated&&mi<0)return acc;                 // mitigated before the window
    if(pi<0)pi=0;                                     // level carried in from before → left edge
    acc.push(Object.assign({},r,{pivot_index:pi,mitigation_index:mi}));return acc;},[]);
  const P=bars[bars.length-1].c;
  let hi=Math.max(...bars.map(b=>b.h)), lo=Math.min(...bars.map(b=>b.l));
  const pad=(hi-lo)*0.06||10; const pmax=hi+pad, pmin=lo-pad;
  const inWin=p=>p!=null&&p>=pmin&&p<=pmax;
  const H=600,padT=10,plotH=H-20,y=p=>padT+(pmax-p)/(pmax-pmin)*plotH;
  const CW0=chart.clientWidth||900, AXIS=70, PW=CW0-AXIS, BW=bars.length?PW/bars.length:0;
  const rightPct=(PW/CW0)*100, xpct=i=>((i*BW+BW/2)/CW0)*100;
  // gridlines
  const step=Math.max(1,Math.round((pmax-pmin)/8));
  for(let g=Math.ceil(pmin/step)*step; g<=pmax; g+=step){const l=el('div','lvl');l.style.top=y(g)+'px';l.style.borderTop='1px solid var(--line)';l.style.opacity='.4';const a=el('div','axis',F(g));a.style.top=(y(g)-8)+'px';l.appendChild(a);chart.appendChild(l);}
  // candles
  const NS='http://www.w3.org/2000/svg';const svg=document.createElementNS(NS,'svg');
  svg.setAttribute('width',PW);svg.setAttribute('height',H);svg.style.cssText='position:absolute;left:0;top:0;pointer-events:none';
  bars.forEach((b,i)=>{const x=i*BW+BW/2;const up=b.c>=b.o;const col=up?'#26a69a':'#ef5350';
    const w=document.createElementNS(NS,'line');w.setAttribute('x1',x);w.setAttribute('x2',x);w.setAttribute('y1',y(b.h));w.setAttribute('y2',y(b.l));w.setAttribute('stroke',col);w.setAttribute('stroke-width','1');svg.appendChild(w);
    const bw=Math.max(1,BW*0.62),yo=y(b.o),yc=y(b.c);const r=document.createElementNS(NS,'rect');r.setAttribute('x',x-bw/2);r.setAttribute('y',Math.min(yo,yc));r.setAttribute('width',bw);r.setAttribute('height',Math.max(1,Math.abs(yc-yo)));r.setAttribute('fill',col);svg.appendChild(r);});
  chart.appendChild(svg);
  const tag=el('div');tag.style.cssText='position:absolute;left:6px;top:2px;font-family:var(--mono);font-size:10px;color:var(--dim)';tag.textContent=bars.length+' × '+data.tf+' candles';chart.appendChild(tag);
  // HRLR / LRLR overlay (the only overlay) — single show/hide toggle
  if(CUR.showRuns) pools.forEach(r=>{if(r.kind==='STANDARD'||!inWin(r.price)||r.pivot_index==null)return;
    const x0=xpct(r.pivot_index), x1=(r.mitigated&&r.mitigation_index!=null)?xpct(r.mitigation_index):rightPct;
    const isL=r.kind==='LRLR', col=isL?'var(--lrlr)':(r.is_high?'var(--red)':'var(--green)');
    const d=el('div','lvl');d.style.top=y(r.price)+'px';d.style.left=Math.min(x0,x1)+'%';d.style.right='auto';d.style.width=Math.max(0.3,Math.abs(x1-x0))+'%';
    d.style.borderTop=(isL?'2px solid ':'2px dotted ')+col;d.style.opacity=r.mitigated?'.32':'.97';
    if(!r.mitigated){const t=el('div','lab',(isL?(r.is_high?'LRLR·EQH':'LRLR·EQL'):(r.is_high?'HRLR↑':'HRLR↓'))+' '+F2(r.price));t.style.color=col;d.appendChild(t);}
    chart.appendChild(d);});
  // current price
  const now=el('div','now');now.style.top=y(P)+'px';now.appendChild(el('div','nt',F2(P)));chart.appendChild(now);
}
load(); setInterval(load, 20000);
</script></body></html>"""
