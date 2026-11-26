(() => {
  document.querySelectorAll('.reason-choice').forEach(group=>{
    const select=group.querySelector('[name=reason_choice]');
    const notes=group.querySelector('[name=reason_notes]');
    const details=group.querySelector('details');
    const hint=group.querySelector('[data-reason-hint]');
    function sync(){const other=select.value.endsWith(':Other');notes.required=other;hint.textContent=other?'(required for Other)':'(optional)';if(other || notes.value)details.open=true;}
    select.addEventListener('change',sync);sync();
  });
  document.querySelectorAll('[name=interruption_choice]').forEach(select=>{
    const field=select.form.querySelector('[name=interruption_other]');
    function sync(){field.closest('label').hidden=select.value!=='Other';field.required=select.value==='Other';}
    select.addEventListener('change',sync);sync();
  });
})();
