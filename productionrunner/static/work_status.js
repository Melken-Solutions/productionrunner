(() => {
 const bar=document.querySelector('[data-work-status]');if(!bar)return;
 const connection=bar.querySelector('[data-connection]'),save=bar.querySelector('[data-save-state]');let dirty=false,submitting=false,busy=false;
 function changed(e){if(e.target.closest('form[method="post"]')&&!e.target.closest('form[action$="logout"]')){dirty=true;save.textContent='Changes not saved';}}
 document.addEventListener('input',changed);document.addEventListener('change',changed);
 document.addEventListener('submit',e=>{if(e.target.method.toLowerCase()!=='post')return;queueMicrotask(()=>{if(e.defaultPrevented)return;submitting=true;save.textContent='Submitting — wait for confirmation before trying again.';});});
 window.addEventListener('beforeunload',e=>{if(dirty&&!submitting){e.preventDefault();e.returnValue='';}});
 window.addEventListener('pageshow',()=>{submitting=false;});
 async function check(){if(busy||document.hidden)return;busy=true;try{const r=await fetch(bar.dataset.feed,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});if(!r.ok||r.redirected||!(await r.json()).ok)throw Error();connection.textContent='Connected · page loaded '+bar.dataset.loaded;connection.className='';}catch{connection.textContent='Connection or sign-in needs attention · changes may not be saved';connection.className='hot';}finally{busy=false;}}
 window.addEventListener('offline',()=>{connection.textContent='Offline · do not assume changes are saved';connection.className='hot';});window.addEventListener('online',check);document.addEventListener('visibilitychange',check);setInterval(check,15000);check();
})();
