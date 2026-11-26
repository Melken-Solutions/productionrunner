document.querySelectorAll('[data-snippet-composer]').forEach(root=>{
 const select=root.querySelector('select'),preview=root.querySelector('[data-snippet-preview]'),note=root.querySelector('[data-snippet-note]'),field=note.querySelector('textarea');
 function update(){const other=select.value==='other';note.hidden=!other;field.disabled=!other;field.required=other;preview.textContent=other?'Type your message below, then send.':select.selectedOptions[0]?.dataset.body||'Choose a message above. No typing is needed for predefined messages.';}
 select.addEventListener('change',update);update();
});
