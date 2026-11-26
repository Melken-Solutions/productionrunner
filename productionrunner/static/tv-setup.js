"use strict";
let started=false,expiry=0;
const code=document.getElementById('code'),status=document.getElementById('status');
async function tick(){
 try{
  const response=await fetch(started?'/tv/pair/status':'/tv/pair/start',{method:'POST',credentials:'same-origin',body:new URLSearchParams({csrf:document.body.dataset.csrf}),signal:AbortSignal.timeout(10000)});
  if(!response.ok){if(response.status===400){location.reload();return;}throw Error();}
  const data=await response.json();
  if(data.url){location.replace(data.url);return;}
  if(data.code){code.textContent=data.code;expiry=Date.now()+data.expires_in*1000;started=true;}
  if(data.state==='expired'){started=false;code.textContent='Getting new code…';}
  status.textContent=started?`Waiting for admin approval · Code refreshes in ${Math.max(0,Math.ceil((expiry-Date.now())/60000))} min`:'Preparing a new code…';
 }catch(error){status.textContent='Connection interrupted. Retrying automatically…';}
 setTimeout(tick,5000);
}
tick();
