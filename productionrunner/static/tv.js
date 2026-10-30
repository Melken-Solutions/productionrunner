"use strict";
let snapshot = null, lastSuccess = 0, page = 0, loading = false;
const freshness = document.getElementById("freshness");
function text(tag, content, cls) { const el = document.createElement(tag); el.textContent = content; if (cls) el.className=cls; return el; }
function drawRows(id, rows, size, held) {
  const parent=document.getElementById(id); parent.replaceChildren();
  const pages=Math.max(1,Math.ceil(rows.length/size)), current=page%pages;
  document.getElementById(held?"hold-page":"work-page").textContent=`${rows.length} entries · ${current+1}/${pages}`;
  if (!rows.length) parent.append(text("p",held?"No held work.":"No pending work in this view."));
  rows.slice(current*size,(current+1)*size).forEach(row=>{
    const card=text("article","",held?"tv-row held":"tv-row");
    card.append(text("h3",`${row.hot ? '▲ HOT '+row.hot+' · ' : ''}${row.pn} · Rev ${row.revision}`,row.hot?"hot":""));
    card.append(text("p",row.operation));
    if(row.operator)card.append(text("p",`Assigned: ${row.operator}`,"assigned"));
    const counts=held?`${row.held} held`:Object.entries({Ready:row.ready,Upcoming:row.upcoming,Inspection:row.inspection,'Not released':row.planning}).filter(([,v])=>v>0).map(([k,v])=>`${k}: ${v}`).join(' · ');
    card.append(text("p",counts,"counts"));card.append(text("small",`${row.ticket_id} · ${row.accepted}/${row.quantity} accepted`));parent.append(card);
  });
}
function render(){if(!snapshot)return;const rows=snapshot.rows;
  const size=Math.max(1,Math.min(4,Math.floor((window.innerHeight-310)/240)));
  drawRows('work',rows.filter(r=>r.ready||r.upcoming||r.inspection||r.planning),size,false);
  drawRows('holds',rows.filter(r=>r.held),size,true);
  document.getElementById('summary').textContent=`${snapshot.organization} · ${snapshot.station}${snapshot.active?'':' · STATION INACTIVE'} · ${new Set(rows.map(r=>r.ticket_id)).size} jobs · Counts are units · Pages rotate every 12 seconds`;
}
function stale(message){freshness.className='stale';freshness.textContent=message+(snapshot?` Last successful update: ${new Date(snapshot.generated_at).toLocaleTimeString()}.`:' No current data available.');}
async function refresh(){if(loading)return;loading=true;const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),10000);
 try{const response=await fetch(document.body.dataset.feed,{cache:'no-store',credentials:'same-origin',signal:controller.signal});if(!response.ok||response.redirected)throw new Error('Unavailable');const data=await response.json();if(!Array.isArray(data.rows))throw new Error('Invalid response');snapshot=data;lastSuccess=performance.now();freshness.className='';freshness.textContent=`LIVE · Last updated ${new Date(data.generated_at).toLocaleTimeString()} · Refreshes every 15 seconds`;render();}
 catch(error){stale('NOT LIVE — connection or sign-in needs attention. Do not rely on displayed status.');}
 finally{clearTimeout(timer);loading=false;}
}
setInterval(()=>{page++;render();},12000);setInterval(refresh,15000);setInterval(()=>{if(lastSuccess&&performance.now()-lastSuccess>30000)stale('STALE — refresh failed. Do not rely on displayed status.');},1000);
window.addEventListener('resize',render);window.addEventListener('online',refresh);window.addEventListener('offline',()=>stale('OFFLINE — do not rely on displayed status.'));
document.getElementById('fullscreen').addEventListener('click',()=>{if(document.documentElement.requestFullscreen)document.documentElement.requestFullscreen().catch(()=>{});});refresh();
