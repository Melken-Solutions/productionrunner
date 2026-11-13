(() => {
 const form=document.getElementById('release-flow');if(!form)return;
 const data=JSON.parse(document.getElementById('release-data').textContent),status=document.getElementById('release-progress'),recovery=document.getElementById('release-recovery');let running=false;
 const base='/requests/'+encodeURIComponent(data.rid);
 async function state(){const r=await fetch(base+'/release-state',{cache:'no-store'});if(!r.ok||r.redirected)throw Error('Sign-in or request access changed. Reload before continuing.');return r.json();}
 async function send(url,values){const body=new URLSearchParams({csrf:form.elements.csrf.value,reason:'Consolidated document, plan and release review',...values});if(Array.isArray(values.erp_checks)){body.delete('erp_checks');values.erp_checks.forEach(v=>body.append('erp_checks',v));}const r=await fetch(url,{method:'POST',body});if(!r.ok||!r.redirected){const doc=new DOMParser().parseFromString(await r.text(),'text/html');throw Error(doc.querySelector('main')?.textContent.trim()||'Action did not complete. Open the saved request to check its status.');}if(new URL(r.url).pathname==='/login')throw Error('Sign-in expired. Open the saved request to check its status.');return r;}
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(running||!form.reportValidity())return;running=true;form.querySelector('button').disabled=true;
  const tracking=form.elements.tracking.value,lot=form.elements.lot_size.value,serials=form.elements.serials.value,save=form.elements.save_template.checked;
  // Freeze the single reviewed decision while guarded server actions run in sequence.
  [...form.elements].forEach(el=>el.disabled=true);
  try{
   let s=await state();if(s.version!==data.version||s.status!==data.status||s.erp_config_version!==data.erp_config_version)throw Error('Request changed. Reload and review the current plan.');
   if(s.status==='preparing'){status.textContent='Recording document and manufacturing review…';await send(base+'/decision',{version:s.version,action:'approve',confirm:'yes',external_ack:'yes'});s=await state();if(s.version!==data.version+1||s.status!=='approved')throw Error('Request changed during review. Open the saved request.');}
   if(save){status.textContent='Saving reusable part plan…';await send(base+'/plan-template',{version:s.version,template_version:data.template_version,confirm:'yes'});}
   status.textContent='Creating the JobRunner and travelers…';await send('/tickets/new',{request_id:data.rid,request_version:s.version,workflow_version:data.workflow_version,pn:data.pn,revision:data.revision,tracking,lot_size:lot,serials});
   s=await state();if(!s.ticket)throw Error('Check the saved request for its JobRunner before trying again.');const tid=encodeURIComponent(s.ticket.id);recovery.href='/tickets/'+tid;recovery.textContent='Open JobRunner '+s.ticket.id;
   let version=1;if(s.ticket.version!==version)throw Error('The new job changed. Review it before release.');
   if(data.has_erp&&s.ticket.erp_config_version!==data.erp_config_version)throw Error('ERP requirements changed. Review the new job before release.');
   if(data.has_erp){status.textContent='Recording the ERP checks you confirmed…';const refs={};Object.entries(data.erp.values).forEach(([k,v])=>refs['erp_'+k]=v);await send('/tickets/'+tid+'/erp',{version,erp_present:'yes',erp_confirm:'yes',erp_checks:data.erp_checks.map((_,i)=>String(i)),...refs});version++;}
   status.textContent='Running final server checks and releasing to floor…';await send('/tickets/'+tid+'/release',{version,plan_review:'yes'});location.assign('/tickets/'+tid);
  }catch(error){status.textContent='Stopped: '+error.message+' Saved progress is retained. Open the request or job below to check its status; do not assume it was released.';status.classList.add('alert');}
 });
})();
