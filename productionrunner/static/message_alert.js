(() => {
 const link=document.querySelector('[data-message-alert]');if(!link)return;
 const banner=document.createElement('aside');banner.className='message-arrival';banner.hidden=true;banner.setAttribute('aria-label','Message notification');
 const text=document.createElement('p');text.setAttribute('role','status');text.setAttribute('aria-live','polite');
 const open=document.createElement('a');open.href=link.href;open.className='button';open.textContent='Open messages';
 const dismiss=document.createElement('button');dismiss.type='button';dismiss.className='secondary';dismiss.textContent='Dismiss';
 banner.append(text,open,dismiss);document.body.append(banner);
 let dismissed=0,latest=0,busy=false;
 dismiss.addEventListener('click',()=>{dismissed=latest;banner.hidden=true;});
 async function check(){if(document.hidden||busy)return;busy=true;try{
  const r=await fetch(link.dataset.feed,{cache:'no-store',credentials:'same-origin',signal:AbortSignal.timeout(10000)});if(!r.ok||r.redirected)throw Error();
  const data=await r.json();if(!Number.isInteger(data.count)||!Number.isInteger(data.latest))throw Error();
  latest=data.latest;link.querySelector('[data-message-count]').textContent=data.count;link.classList.toggle('hot',data.count>0);link.title='Unread job conversations';
  if(!data.count){banner.hidden=true;dismissed=0;}
  else if(latest!==dismissed){const message=data.count===1?'New message — 1 unread conversation':`New messages — ${data.count} unread conversations`;if(text.textContent!==message)text.textContent=message;banner.hidden=false;}
 }catch{link.querySelector('[data-message-count]').textContent='— not live';link.title='Open Messages to reconnect.';}finally{busy=false;}}
 check();setInterval(check,15000);window.addEventListener('online',check);document.addEventListener('visibilitychange',check);
})();
