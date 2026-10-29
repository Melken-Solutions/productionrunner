(() => {
  const search=document.getElementById('catalog-search');
  const status=document.getElementById('catalog-status');
  const entries=[...document.querySelectorAll('[data-entry]')];
  function filter(){
    let visible=0;
    for(const row of entries){
      const matches=(!search || row.querySelector('summary').textContent.toLowerCase().includes(search.value.trim().toLowerCase())) && (!status || status.value==='all' || status.value===row.dataset.status);
      row.hidden=!matches;if(matches)visible++;
    }
    const count=document.getElementById('catalog-count');if(count)count.textContent=`${visible} of ${entries.length}`;
    const empty=document.getElementById('catalog-empty');if(empty)empty.hidden=visible!==0;
  }
  search?.addEventListener('input',filter);status?.addEventListener('change',filter);filter();
  document.addEventListener('invalid',e=>{let el=e.target.parentElement;while(el){if(el.tagName==='DETAILS')el.open=true;el=el.parentElement;}},true);
})();
