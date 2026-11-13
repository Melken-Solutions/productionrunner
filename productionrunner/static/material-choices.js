(() => {
  const scoped=['alloy','gauge','thickness_mm','finish'];
  document.querySelectorAll('[data-material-form]').forEach(form=>{
    const metal=form.elements.metal;
    const original=Object.fromEntries([...form.querySelectorAll('[data-material-property]')].map(el=>[el.name,el.value]));
    const options=Object.fromEntries(scoped.map(k=>[k,[...form.elements[k].options].map(o=>o.cloneNode(true))]));
    function sync(){
      for(const field of scoped){
        const select=form.elements[field];
        const previous=select.value;
        const available=options[field].filter(option=>!option.value || (option.dataset.metal===metal.value && (option.dataset.active==='1' || (metal.value===original.metal && option.value===original[field]))));
        select.replaceChildren(...available.map(o=>o.cloneNode(true)));
        select.value=available.some(o=>o.value===previous)?previous:'';
      }
      if(form.dataset.existing==='no')form.elements.name.value=['gauge','metal','alloy','finish','thickness_mm','stock_size','required_service'].map(k=>form.elements[k].value).filter(Boolean).join(' · ');
    }
    form.addEventListener('change',sync);sync();
  });
  const field=document.getElementById('choice-field');
  if(field){const select=document.getElementById('choice-metal');const label=document.getElementById('choice-metal-label');const sync=()=>{label.hidden=!scoped.includes(field.value);select.disabled=label.hidden;select.required=!label.hidden;};field.addEventListener('change',sync);sync();}
})();
