(() => {
 const link=document.querySelector('[data-message-alert]');if(!link)return;
 async function check(){if(document.hidden)return;try{const r=await fetch(link.dataset.feed,{cache:'no-store',credentials:'same-origin',signal:AbortSignal.timeout(10000)});if(!r.ok||r.redirected)throw Error();const data=await r.json();if(!Number.isInteger(data.count))throw Error();link.querySelector('[data-message-count]').textContent=data.count;link.classList.toggle('hot',data.count>0);link.title='Unread job conversations';}catch{link.querySelector('[data-message-count]').textContent='— not live';link.title='Open Messages to reconnect.';}}
 setInterval(check,15000);window.addEventListener('online',check);
})();
