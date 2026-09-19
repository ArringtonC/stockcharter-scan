/* Ledger redesign — shared review chrome for every Before/After page.
   A page supplies: a #seg-state button group, renderAfter(d,state), shape(state).
   This file owns everything the eight pages have in common. */

const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f2=x=>Number(x).toFixed(2);
const pct=(x,sign=true)=>x==null?'—':(sign&&Number(x)>0?'+':'')+Number(x).toFixed(1)+'%';
const money=x=>'$'+Number(x).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const n0=x=>'$'+Math.round(Math.abs(x)).toLocaleString();

let LIVE=null, STATE='live';

function paint(){
  if(STATE==='loading'||STATE==='error'||!LIVE){ renderAfter(LIVE||{},STATE); return; }
  renderAfter(shape(STATE),STATE);
}

/* ── switchers ───────────────────────────────────────────────────────────── */
function seg(id,fn){
  document.querySelectorAll(`#${id} button`).forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll(`#${id} button`).forEach(x=>x.setAttribute('aria-pressed',x===b));
    fn(b);
  }));
}
seg('seg-state',b=>{STATE=b.dataset.s;paint();});
seg('seg-v',b=>{document.body.dataset.v=b.dataset.v;});
seg('seg-w',b=>{document.body.dataset.w=b.dataset.w;});
seg('seg-t',b=>{document.documentElement.dataset.theme=b.dataset.t;themeFrame(b.dataset.t);});

/* ── the BEFORE pane ─────────────────────────────────────────────────────────
   It is the real index.html, unmodified on disk. Two things are done to it from
   here so the comparison is fair:
     1. index.html restores your last tab from localStorage, so it can open on the
        wrong one. Each page names the tab it compares via data-tab on the iframe.
     2. index.html has no [data-theme] override yet — that is one of the CHANGES
        being proposed — so the light toggle is injected into the frame rather than
        edited into the live file. */
const LIGHT=`--bg:#F4F1EA;--bg2:#ECE8DF;--panel:#FBF9F4;--line:#D9D3C6;--line2:#C6BFAF;
  --ink:#17181C;--ink2:#4A4C55;--dim:#8A877E;--amber:#9A6A12;--amber2:#7A5208;--up:#1E6E44;--dn:#B23A2A;`;
const DARK=`--bg:#0E0F12;--bg2:#141518;--panel:#191A1F;--line:#26282F;--line2:#33363F;
  --ink:#E8E4DB;--ink2:#A8A49B;--dim:#6E6B64;--amber:#E2A73B;--amber2:#F5C866;--up:#5FBF84;--dn:#E06B5A;`;

function themeFrame(t){
  const f=document.getElementById('beforeframe'); if(!f) return;
  try{
    const doc=f.contentWindow.document;
    let s=doc.getElementById('__review_theme');
    if(!s){ s=doc.createElement('style'); s.id='__review_theme'; doc.head.appendChild(s); }
    s.textContent=`:root{${t==='light'?LIGHT:DARK}}`;
  }catch(e){}
}

(function(){
  const f=document.getElementById('beforeframe'); if(!f) return;
  const note=document.getElementById('beforenote'), want=f.dataset.tab||'scan';
  f.addEventListener('load',()=>{
    try{
      const b=f.contentWindow.document.querySelector(`nav [data-tab="${want}"]`);
      if(b) b.click(); else throw new Error('no tab');
      themeFrame(document.documentElement.dataset.theme);
    }catch(e){ if(note) note.innerHTML=`· <span class="synth">open the ${want} tab in this pane</span>`; }
  });
})();

/* ── boot ────────────────────────────────────────────────────────────────── */
document.documentElement.dataset.theme='dark';
document.body.dataset.v='both';
document.body.dataset.w='wide';
renderAfter({},'loading');
fetch('../data.json?'+Date.now()).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json()})
  .then(d=>{LIVE=d;paint();})
  .catch(()=>{STATE='error';paint();});
