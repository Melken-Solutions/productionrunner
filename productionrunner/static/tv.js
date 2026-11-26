"use strict";
let snapshot=null,lastSuccess=0,page=0,loading=false,settings=JSON.parse(document.getElementById('initial-settings').textContent),nextPage=performance.now()+20000,signature='';
const freshness=document.getElementById('freshness');
function text(tag,content,cls){const el=document.createElement(tag);el.textContent=content;if(cls)el.className=cls;return el;}
function card(row){
 const el=text('article','',row.held?'tv-row held':'tv-row');el.dataset.ticket=row.ticket_id;
 el.append(text('h3',`${row.hot!==null?'▲ HOT '+row.hot+' · ':''}${row.pn} · Rev ${row.revision}`,row.hot!==null?'hot':''));
 el.append(text('p',row.operation||'Production'));
 const counts={Ready:Math.max(0,(row.ready||0)-(row.running||0)-(row.paused||0)),Running:row.running,Paused:row.paused,Setup:row.setup,Inspection:row.inspection,Held:row.held,Upcoming:Math.max(0,(row.upcoming||0)-(row.setup||0)-(row.blocked||0)),Blocked:row.blocked,'Not released':row.planning};
 el.append(text('p',Object.entries(counts).filter(([,v])=>v>0).map(([k,v])=>`${k}: ${v}`).join(' · ')||`${row.accepted} accepted`,'counts'));
 if(row.shipment&&!row.shipment.met)el.append(text('p',`Ship ${row.shipment.remaining} more by ${row.shipment.ship_date}`));else if(row.due)el.append(text('p',`Required ${row.due}`));
 if(settings.show_start&&row.planned_day)el.append(text('p',`Start ${row.planned_day}`));
 if(settings.show_operator&&row.operator)el.append(text('p',`Assigned: ${row.operator}`));
 el.append(text('small',`${row.ticket_id} · ${row.accepted}/${row.quantity} accepted`));return el;
}
function pages(rows,container){
 const measure=document.createElement('div');measure.className='measure';measure.style.width=container.clientWidth+'px';document.body.append(measure);
 const height=container.clientHeight,result=[];let chunk=[],used=0;
 for(const row of rows){const el=card(row);measure.replaceChildren(el);const h=el.getBoundingClientRect().height;
  if(chunk.length&&(used+12+h>height||chunk.length>=settings.cards)){result.push(chunk);chunk=[];used=0;}
  chunk.push(row);used+=(used?12:0)+h;
 }
 if(chunk.length)result.push(chunk);measure.remove();return result.length?result:[[]];
}
function lane(id,rows,label){const el=document.getElementById(id);el.replaceChildren(...rows.map(card));if(!rows.length)el.append(text('p','No pending work.','empty'));document.getElementById(id+'-page').textContent=label;}
function render(forceSingle=false){if(!snapshot)return;
 document.body.dataset.text=settings.text_size;
 const portrait=settings.orientation==='portrait'||(settings.orientation==='auto'&&innerHeight>innerWidth);
 const single=forceSingle===true||settings.layout==='single'||portrait||innerWidth<700;
 const board=document.getElementById('board');board.classList.toggle('single',single);document.getElementById('right-lane').hidden=single;
 const s=snapshot.summary;document.getElementById('summary').replaceChildren(...[['Jobs',s.jobs],['HOT',s.hot],['Due / overdue (UTC)',s.due],['Held',s.held],['Awaiting pack / ship',s.shipping]].map(([label,value])=>text('span',`${label}: ${value}`,label==='HOT'?'hot':'')));
 const rows=snapshot.rows,hot=rows.filter(r=>r.hot!==null),normal=rows.filter(r=>r.hot===null),split=!single&&settings.layout==='priority'&&hot.length&&normal.length;
 document.getElementById('left-title').textContent=split?'▲ HOT jobs':'Production queue';document.getElementById('right-title').textContent=split?'Normal production':'Production queue — continued';
 if(split){for(const [id,list] of [['left',hot],['right',normal]]){const groups=pages(list,document.getElementById(id)),n=page%groups.length;lane(id,groups[n],`${list.length} entries · Page ${n+1} of ${groups.length}`);}}
 else{const groups=pages(rows,document.getElementById('left')),count=single?1:2,total=Math.ceil(groups.length/count),n=page%total;lane('left',groups[n*count],`${rows.length} entries · Page ${n+1} of ${total}${single?'':' · read left, then right'}`);if(!single)lane('right',groups[n*count+1]||[],`Page ${n+1} of ${total}`);}
 const clipped=[...document.querySelectorAll('.cards .tv-row')].some(el=>el.getBoundingClientRect().bottom>el.parentElement.getBoundingClientRect().bottom+1);
 if(clipped&&!single){render(true);return;}
 if(clipped){document.getElementById('left').replaceChildren(text('p','This window is too short for the selected text and details. Use full screen, hide optional fields, or choose a smaller text preset.','empty'));}
 if(forceSingle===true)document.getElementById('left-page').textContent+=' · One column to preserve readability';
 document.getElementById('rotation').textContent=`Next page in ${Math.max(0,Math.ceil((nextPage-performance.now())/1000))}s`;
 if(!snapshot.active&&!freshness.textContent.includes('STATION INACTIVE'))freshness.textContent+=' · STATION INACTIVE';
}
function stale(message){freshness.className='stale';freshness.textContent=message+(snapshot?` Last update: ${new Date(snapshot.generated_at).toLocaleTimeString()}.`:' No data available.');}
async function refresh(){if(loading)return;loading=true;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
 try{const response=await fetch(document.body.dataset.feed,{cache:'no-store',credentials:'same-origin',signal:controller.signal});if(!response.ok||response.redirected)throw Error();const data=await response.json();if(!Array.isArray(data.rows)||!data.settings||!data.summary)throw Error();snapshot=data;lastSuccess=performance.now();settings=data.settings;const next=JSON.stringify(settings);if(next!==signature){signature=next;page=0;nextPage=performance.now()+settings.seconds*1000;}freshness.className='';freshness.textContent=`LIVE · Updated ${new Date(data.generated_at).toLocaleTimeString()} · Refreshes every 15 seconds`;render();}
 catch(e){stale('NOT LIVE — connection or sign-in needs attention. Do not rely on displayed status.');}finally{clearTimeout(timer);loading=false;}}
setInterval(()=>{const now=performance.now();if(snapshot&&now>=nextPage){page++;nextPage=now+settings.seconds*1000;render();}document.getElementById('rotation').textContent=`Next page in ${Math.max(0,Math.ceil((nextPage-now)/1000))}s`;if(lastSuccess&&now-lastSuccess>30000)stale('STALE — do not rely on displayed status.');},1000);
setInterval(refresh,15000);window.addEventListener('resize',render);document.fonts.ready.then(render);window.addEventListener('online',refresh);window.addEventListener('offline',()=>stale('OFFLINE — do not rely on displayed status.'));
document.getElementById('fullscreen').addEventListener('click',()=>document.documentElement.requestFullscreen?.().catch(()=>{}));refresh();
