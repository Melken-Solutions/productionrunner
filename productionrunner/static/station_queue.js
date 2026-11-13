(() => {
  const root=document.querySelector('[data-station-queue]');if(!root)return;
  const status=root.querySelector('[data-queue-freshness]');if(!status)return;
  let busy=false;
  async function refresh(){
    if(busy||document.hidden||root.querySelector('details[open]')||root.contains(document.activeElement)&&document.activeElement.matches('input,select,button'))return;
    busy=true;
    try{const response=await fetch(location.href,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(10000)});if(!response.ok||response.redirected)throw Error();const doc=new DOMParser().parseFromString(await response.text(),'text/html');const results=doc.querySelector('[data-queue-results]');if(!results)throw Error();root.querySelector('[data-queue-results]').replaceChildren(...results.childNodes);status.textContent='Updated '+new Date().toLocaleTimeString()+' · Same station queue as the TV';status.classList.remove('alert');}
    catch{status.textContent='Not live — reconnect or sign in again before relying on this queue.';status.classList.add('alert');}
    finally{busy=false;}
  }
  window.addEventListener('offline',()=>{status.textContent='Offline — queue may be out of date.';status.classList.add('alert');});window.addEventListener('online',refresh);setInterval(refresh,15000);
})();
