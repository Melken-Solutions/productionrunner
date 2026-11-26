document.querySelectorAll('.view-switcher').forEach(menu=>{
 document.addEventListener('click',e=>{if(!menu.contains(e.target))menu.open=false;});
 menu.addEventListener('keydown',e=>{if(e.key==='Escape'){menu.open=false;menu.querySelector('summary').focus();}});
});
