/* Market Context tab (2026-10-27, v2 2026-10-28). Renders docs/context.json. Information only: it changes no signal.
   Clean chart per stock (button): candles + weekly structure levels only, drawn from data.json's chart bars. */
(function(){
const money=n=>'$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const pct=(a,b)=>((a/b-1)*100);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const WORD={'Bullish':'Rising','Bearish':'Falling','Range':'Sideways','Transitioning up':'Turning up','Transitioning down':'Turning down','Insufficient data':'Not enough data'};
const TONE={'Bullish':'up','Bearish':'dn','Range':'flat','Transitioning up':'turn','Transitioning down':'turn','Insufficient data':'none'};
const TF=[['Monthly','Long term','monthly'],['Weekly','Medium term','weekly'],['Daily','Short term','daily']];
function warn(detail){
  if(/closed below the last higher low/.test(detail))return 'uptrend just broke';
  if(/closed above the last lower high/.test(detail))return 'downtrend just broke';
  if(/below the last confirmed low/.test(detail))return 'now below its last low';
  if(/above the last confirmed high/.test(detail))return 'now above its last high';
  return '';
}
function group(o){
  if(o.error)return 3;
  const L=TF.map(([k])=>o.blocks[k].label);
  if(L.every(l=>l==='Bullish'))return 0;
  if(L.filter(l=>l==='Insufficient data').length>=2)return 3;
  if(L.filter(l=>l==='Bearish'||l==='Transitioning down').length>=2)return 2;
  return 1;
}
const GROUPS=['Rising on every timeframe','Mixed','Falling on two or more timeframes','Not enough data'];
function plain(o){
  const B=o.blocks,w=B.Weekly,word=k=>WORD[B[k].label].toLowerCase();
  const L=TF.map(([k])=>B[k].label);
  let s=L.every(l=>l===L[0])?`${WORD[L[0]]} on every timeframe.`:`Long term ${word('Monthly')}, medium term ${word('Weekly')}, short term ${word('Daily')}.`;
  if(w&&w.hi){
    if(w.range_break==='Above upper boundary')s+=` It has broken above its recent weekly range (top ${money(w.hi.p)}). Not confirmed yet.`;
    else if(w.range_break==='Below lower boundary')s+=` It has broken below its recent weekly range (bottom ${money(w.lo.p)}). Not confirmed yet.`;
    else s+=` It is trading inside its recent weekly range of ${money(w.lo.p)}–${money(w.hi.p)}.`;
    if(w.outcome&&w.outcome.startsWith('Failed'))s+=' A recent break failed and price came back inside.';
  }
  return s;
}
function levelsLine(o){
  const w=o.blocks.Weekly; if(!w||!w.hi)return '';
  const r=w.res!=null?`<span><span class="cx-k">Next resistance</span>${money(w.res)} <em class="cx-dn">${pct(w.res,o.close).toFixed(1)}% above</em></span>`:`<span><span class="cx-k">Next resistance</span>none above</span>`;
  const s=w.sup!=null?`<span><span class="cx-k">Support</span>${money(w.sup)} <em class="cx-up">${Math.abs(pct(w.sup,o.close)).toFixed(1)}% below</em></span>`:`<span><span class="cx-k">Support</span>none below</span>`;
  return `<div class="cx-levels">${r}${s}<span><span class="cx-k">Weekly range</span>${money(w.lo.p)} – ${money(w.hi.p)}</span></div>`;
}
function strength(o){
  const r=o.rs||{},r6=r.ret6m==null?'':`${r.ret6m>=0?'+':''}${(r.ret6m*100).toFixed(1)}% in 6 months`;
  if(r.pct==null)return `<div class="cx-rs"><span class="cx-k">Strength</span><span>${r6||'—'} · not in the S&amp;P 500, so no ranking</span></div>`;
  return `<div class="cx-rs"><span class="cx-k">Strength</span><span class="cx-rsbar" aria-hidden="true"><span style="left:${r.pct}%"></span></span><span>Stronger than <b>${r.pct}%</b> of S&amp;P 500 stocks · ${r6}</span></div>`;
}
function pills(o){
  return `<div class="cx-pills">${TF.map(([k,name,tf])=>{const b=o.blocks[k],wn=warn(b.detail);
    return `<div class="cx-pill cx-${TONE[b.label]}" title="${esc(k+': '+b.label+' — '+b.detail)}"><span class="cx-k">${name} <small>${tf}</small></span><b>${WORD[b.label]}</b>${wn?`<small class="cx-warn">${wn}</small>`:''}</div>`}).join('')}</div>`;
}
function scale(o){
  const w=o.blocks.Weekly; if(!w||!w.hi) return '';
  const pend=(w.unconfirmed||[]).filter(u=>u.kind==='high'?u.p>w.hi.p:u.p<w.lo.p);
  const vals=[w.hi.p,w.lo.p,o.close,w.res,w.sup,...pend.map(p=>p.p)].filter(v=>v!=null);
  const lo=Math.min(...vals),hi=Math.max(...vals),pad=(hi-lo)*0.06||1,a=lo-pad,b=hi+pad,x=v=>((v-a)/(b-a)*100).toFixed(2)+'%';
  let h=`<div class="cx-rail"></div><div class="cx-band" style="left:${x(w.lo.p)};width:calc(${x(w.hi.p)} - ${x(w.lo.p)})"></div>`;
  const marks=[];
  if(w.res!=null&&w.res!==w.hi.p)marks.push(['res','resistance '+money(w.res),w.res]);
  if(w.sup!=null&&w.sup!==w.lo.p)marks.push(['sup','support '+money(w.sup),w.sup]);
  pend.forEach(p=>marks.push(['pend','not confirmed '+money(p.p),p.p]));
  const rows=[-1e9,-1e9,-1e9];
  marks.sort((p,q)=>p[2]-q[2]).forEach(([c,t,v])=>{
    const xv=(v-a)/(b-a)*100; let r=rows.findIndex(last=>xv-last>22); if(r<0)r=rows.length-1; rows[r]=xv;
    h+=`<div class="cx-mk cx-${c}" style="left:${x(v)};top:${r*15}px">${t}<i style="height:${44-r*15-14}px"></i></div>`;});
  h+=`<div class="cx-now" style="left:${x(o.close)}"><b>${money(o.close)}</b></div>`;
  return `<span class="cx-k">Weekly range (shaded) and nearby levels</span><div class="cx-scale" role="img" aria-label="Weekly range ${money(w.lo.p)} to ${money(w.hi.p)}, price ${money(o.close)}">${h}</div>`;
}
function details(o){
  const B=o.blocks,w=B.Weekly;
  const facts=w&&w.hi?`<div class="cx-facts">
    <div><span class="cx-k">Range break (weekly)</span>${esc(w.range_break)}</div>
    <div><span class="cx-k">New swing confirmed?</span>${esc(w.swing_conf)}</div>
    <div><span class="cx-k">Holding or failed?</span>${esc(w.outcome)}</div></div>`:'';
  const rows=TF.map(([k,name])=>{const b=B[k];if(!b.hi)return `<tr><td>${name}<small>${k}</small></td><td colspan="4">not enough data</td></tr>`;
    const un=(b.unconfirmed||[]).map(u=>`<span class="cx-unc">${u.kind} ${money(u.p)} · ${u.pivot} · needs ${u.need} more</span>`).join('<br>')||'—';
    return `<tr><td>${name}<small>${k} · ${esc(b.label)}</small></td><td>${money(b.hi.p)}<small>${b.hi.pivot} → confirmed ${b.hi.conf}</small></td><td>${money(b.lo.p)}<small>${b.lo.pivot} → confirmed ${b.lo.conf}</small></td><td>${un}</td><td>${b.res!=null?money(b.res):'—'} / ${b.sup!=null?money(b.sup):'—'}</td></tr>`}).join('');
  return `${scale(o)}${facts}
    <div class="cx-tbl"><table><thead><tr><th>Timeframe</th><th>Last confirmed high</th><th>Last confirmed low</th><th>Not confirmed yet</th><th>Resistance / support</th></tr></thead><tbody>${rows}</tbody></table></div>
    <p class="cx-fine">A swing high or low counts only after it is confirmed: about 3 trading days later on the daily chart, about 13 on the weekly, about 53 on the monthly. The current week and month are not used until they finish.</p>`;
}
function card(o,i){
  if(o.error)return `<article class="cx-card" id="cx-${esc(o.sym)}"><div class="cx-top"><div class="cx-id"><span class="cx-sym">${esc(o.sym)}</span></div></div><p class="cx-say">${esc(o.error)}</p></article>`;
  return `<article class="cx-card" id="cx-${esc(o.sym)}">
    <div class="cx-top">
      <div class="cx-id"><span class="cx-sym">${esc(o.sym)}</span><span class="cx-px">${money(o.close)}</span><span class="cx-fresh ${o.stale?'cx-stale':''}">${o.stale?'old data · '+o.asof:'close '+o.asof}</span></div>
      <div class="cx-btns"><button type="button" id="cx-cbtn-${i}" class="cx-btn" data-act="chart" data-i="${i}" aria-expanded="false" aria-controls="cx-chart-${i}">Chart</button><button type="button" id="cx-dbtn-${i}" class="cx-btn" data-act="details" data-i="${i}" aria-expanded="false" aria-controls="cx-det-${i}">Details</button></div>
    </div>
    ${pills(o)}
    <p class="cx-say">${esc(plain(o))}</p>
    ${levelsLine(o)}
    ${strength(o)}
    <div class="cx-chartwrap" id="cx-chart-${i}" hidden>
      <div class="cx-rng" role="group" aria-label="Chart range">${['3M','6M','1Y','2Y'].map(r=>`<button type="button" class="cx-rbtn" data-r="${r}" aria-pressed="${r==='6M'}">${r}</button>`).join('')}<span class="cx-legend"><span><i class="cx-l-edge"></i>weekly range</span><span><i class="cx-l-res"></i>resistance</span><span><i class="cx-l-sup"></i>support</span><span><i class="cx-l-pend"></i>not confirmed</span></span></div>
      <div class="cx-chart"></div>
    </div>
    <div class="cx-det" id="cx-det-${i}" hidden>${details(o)}</div>
  </article>`;
}
function glance(items){
  return `<div class="cx-glance" role="list" aria-label="At a glance">${items.map(o=>{
    if(o.error)return `<a role="listitem" href="#cx-${esc(o.sym)}" class="cx-row"><b>${esc(o.sym)}</b><span class="cx-dots"></span><span class="cx-rk">—</span></a>`;
    const dots=TF.map(([k,name])=>`<i class="cx-dot cx-${TONE[o.blocks[k].label]}" title="${name}: ${WORD[o.blocks[k].label]}"></i>`).join('');
    const r=o.rs&&o.rs.pct!=null?`${o.rs.pct}%`:'—';
    return `<a role="listitem" href="#cx-${esc(o.sym)}" class="cx-row"><b>${esc(o.sym)}</b><span class="cx-dots" aria-label="long, medium, short term">${dots}</span><span class="cx-rk" title="stronger than this share of S&amp;P 500 stocks over 6 months">${r}</span></a>`;}).join('')}</div>
    <p class="cx-fine cx-key">Dots = long · medium · short term: <span><i class="cx-dot cx-up"></i>rising</span> <span><i class="cx-dot cx-dn"></i>falling</span> <span><i class="cx-dot cx-flat"></i>sideways</span> <span><i class="cx-dot cx-turn"></i>turning</span> · % = stronger than that share of S&amp;P 500 stocks over 6 months. Tap a name to jump to it.</p>`;
}

/* ---- clean chart ---- */
const CHARTS=new Map();
function tokens(){const r=getComputedStyle(document.documentElement),c=k=>r.getPropertyValue(k).trim();
  return {bg:c('--panel')||'#191A1F',line:c('--line'),line2:c('--line2'),ink:c('--ink'),ink2:c('--ink2'),dim:c('--dim'),up:c('--up'),dn:c('--dn'),amber:c('--amber')};}
function drawChart(i,o,host){
  const L=window.LightweightCharts,s=(window.LEDGER_CHARTS||{})[o.sym];
  if(!L||!s||!s.d||!s.d.length){host.innerHTML='<p class="cx-fine">No chart data for this name in today\'s scan.</p>';return;}
  const C=tokens(),narrow=host.clientWidth<520;
  const api=L.createChart(host,{width:host.clientWidth,height:narrow?260:340,
    layout:{background:{type:L.ColorType.Solid,color:C.bg},textColor:C.dim,fontFamily:"'IBM Plex Mono', ui-monospace, Menlo, monospace",fontSize:11},
    grid:{vertLines:{visible:false},horzLines:{color:C.line}},
    rightPriceScale:{borderColor:C.line2,scaleMargins:{top:.08,bottom:.06}},timeScale:{borderColor:C.line2,rightOffset:4},
    crosshair:{mode:L.CrosshairMode.Normal},handleScroll:{vertTouchDrag:false}});
  const ser=api.addSeries(L.CandlestickSeries,{upColor:C.up,downColor:C.dn,borderVisible:false,wickUpColor:C.up,wickDownColor:C.dn,priceLineVisible:false});
  ser.setData(s.d.map((t,k)=>({time:t,open:+s.o[k],high:+s.h[k],low:+s.l[k],close:+s.c[k]})));
  (window.contextLines?window.contextLines(o.sym):[]).forEach(([title,price,k])=>ser.createPriceLine({price:+price,lineWidth:1,axisLabelVisible:true,title:narrow?'':title,
    color:k==='res'?C.dn:k==='sup'?C.up:k==='pend'?C.ink2:C.amber,lineStyle:k==='pend'?L.LineStyle.Dashed:L.LineStyle.Dotted}));
  const n=s.d.length,setR=r=>{const bars={'3M':63,'6M':126,'1Y':252,'2Y':n}[r]||126;api.timeScale().setVisibleLogicalRange({from:Math.max(0,n-bars),to:n+3});};
  setR('6M');
  const ro=new ResizeObserver(()=>{if(host.clientWidth>0)api.applyOptions({width:host.clientWidth})});ro.observe(host);
  CHARTS.set(i,{api,ro,setR});
}
function toggle(root,i,act){
  const o=root.__items[i],btn=root.querySelector(`[data-act="${act}"][data-i="${i}"]`),panel=document.getElementById((act==='chart'?'cx-chart-':'cx-det-')+i);
  const open=panel.hidden; panel.hidden=!open; btn.setAttribute('aria-expanded',String(open));
  if(act==='chart'){
    if(open&&!CHARTS.has(i))drawChart(i,o,panel.querySelector('.cx-chart'));
    if(!open&&CHARTS.has(i)){const c=CHARTS.get(i);c.ro.disconnect();c.api.remove();CHARTS.delete(i);panel.querySelector('.cx-chart').innerHTML='';
      panel.querySelectorAll('.cx-rbtn').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.r==='6M')));}
  }
}

window.renderContext=function(el,c){
  if(!el)return;
  CHARTS.forEach(x=>{x.ro.disconnect();x.api.remove()});CHARTS.clear();
  if(!c||!c.items){el.innerHTML=`<div class="cxroot"><div class="cx-head"><h2>Market context</h2></div><p class="cx-fine">No context data yet. It is written after each scan.</p></div>`;return;}
  const items=c.items.slice().sort((a,b)=>group(a)-group(b)||((b.rs&&b.rs.pct)??-1)-((a.rs&&a.rs.pct)??-1));
  let body='',g=-1;
  items.forEach((o,i)=>{const gi=group(o);if(gi!==g){g=gi;body+=`<h3 class="cx-group">${GROUPS[gi]} <span>${items.filter(x=>group(x)===gi).length}</span></h3>`;}body+=card(o,i);});
  el.innerHTML=`<div class="cxroot">
    <div class="cx-head"><h2>Market context</h2><span class="cx-tag">information only · never changes a signal</span></div>
    <p class="cx-lede">How each Focus stock is trending, where its key prices sit, and how strong it has been. Prices as of the ${esc(c.last_session)} close. Trend and strength were tested as trading signals and showed no edge, so read this as a map, not advice.</p>
    ${glance(items)}${body}</div>`;
  const root=el.querySelector(".cxroot"); root.__items=items;
  root.addEventListener('click',e=>{
    const b=e.target.closest('.cx-btn'); if(b){toggle(root,+b.dataset.i,b.dataset.act);return;}
    const r=e.target.closest('.cx-rbtn'); if(r){const w=r.closest('.cx-chartwrap'),i=+w.id.split('-').pop(),c2=CHARTS.get(i);
      w.querySelectorAll('.cx-rbtn').forEach(x=>x.setAttribute('aria-pressed',String(x===r)));if(c2)c2.setR(r.dataset.r);return;}
    const a=e.target.closest('.cx-row'); if(a){e.preventDefault();const t=document.getElementById(a.getAttribute('href').slice(1));if(t)t.scrollIntoView({behavior:'smooth',block:'start'});}
  });
};
/* Levels for any chart: weekly confirmed high/low, nearest resistance/support, unconfirmed weekly swing. */
window.contextLines=function(sym){
  const o=(window.CTX&&window.CTX.items||[]).find(x=>x.sym===sym), w=o&&o.blocks&&o.blocks.Weekly;
  if(!w||!w.hi)return [];
  const L=[['WK HIGH',w.hi.p,'edge'],['WK LOW',w.lo.p,'edge']];
  if(w.res!=null&&w.res!==w.hi.p)L.push(['RESIST',w.res,'res']);
  if(w.sup!=null&&w.sup!==w.lo.p)L.push(['SUPPORT',w.sup,'sup']);
  (w.unconfirmed||[]).forEach(u=>L.push([u.kind==='high'?'WK HIGH?':'WK LOW?',u.p,'pend']));
  return L;
};
})();
