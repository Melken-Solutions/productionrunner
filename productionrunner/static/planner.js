(() => {
  const form = document.getElementById('planner-form');
  if (!form) return;
  const panels = [...form.querySelectorAll('[data-panel]')];
  const next = document.getElementById('planner-next');
  const back = document.getElementById('planner-back');
  const review = document.getElementById('planner-review');
  let stage = 0;
  const value = name => form.elements.namedItem(name)?.value?.trim() || '';
  const selected = name => form.elements.namedItem(name)?.selectedOptions?.[0]?.textContent || 'Not selected';
  const node = (tag, text, cls) => { const el = document.createElement(tag); el.textContent = text; if (cls) el.className = cls; return el; };
  function plan() { try { return JSON.parse(document.getElementById('request-plan-json').value); } catch { return {steps:[]}; } }
  function renderReview() {
    const data = plan();
    const route = document.getElementById('planner-route'); route.replaceChildren();
    const names = (data.steps || []).map(s => s.name || 'Name this step');
    if (data.steps?.some(s => s.outside)) names.push('Inbound inspection');
    names.push('Inspection','Packing','Shipping');
    names.forEach((name, i) => { if (i) route.append(node('b','→')); route.append(node('span',name)); });
    review.replaceChildren();
    function card(title, lines, target) {
      const box = node('section','','planner-review-item'); box.append(node('h3',title));
      lines.forEach(line => box.append(node('p',line)));
      const edit = node('button','Edit','secondary'); edit.type='button'; edit.addEventListener('click',()=>show(target)); box.append(edit);review.append(box);
    }
    card(`${value('pn') || 'Choose a part'} · Rev ${value('revision') || '—'}`, [`${value('quantity') || '—'} ${value('quantity')==='1'?'unit':'units'} · Due ${value('required_date') || 'not specified'}`, `Material: ${selected('material_id')}`,value('description') || 'Describe the requested work',`Customer: ${value('requester') || 'needed'} · Destination: ${value('destination') || 'needed'}`],0);
    const shopDetails=[...form.querySelectorAll('[data-custom-label]')].map(el=>`${el.dataset.customLabel}: ${el.value || 'Not specified'}`);
    if(shopDetails.length)card('Shop details',shopDetails,0);
    card('Production route', names.map((name,i)=>`${i+1}. ${name}`),1);
    if (value('outside_required') && form.elements.outside_required.checked) card('Outside processing', [value('outside_vendor') || 'Choose a vendor',value('outside_service') || 'Choose a service',`Expected return: ${value('outside_due_date') || 'needed'}`],1);
    const unfinished = !data.steps?.length || data.steps.some(s=>!s.name||!s.instructions||!s.criteria||(!s.outside&&!s.station_id)) || ['inspection','packing','shipping'].some(k=>!data[k]?.station_id||!data[k]?.criteria);
    if (unfinished) review.append(node('p','Some station assignments, instructions or checks are still missing. You can save a draft and complete them before approval.','planner-warning'));
    review.append(node('p', data.dxf_required ? 'DXF required before approval.' : 'DXF is not required by this plan.','muted'));
  }
  function show(index) {
    stage=index; panels.forEach((p,i)=>p.hidden=i!==stage);
    document.querySelectorAll('[data-go]').forEach(b=>{ if (+b.dataset.go===stage) b.setAttribute('aria-current','step'); else b.removeAttribute('aria-current'); });
    back.hidden=stage===0; next.hidden=stage===2;
    next.textContent=stage===0?'Continue to Job Plan →':'Review request →';
    document.getElementById('planner-position').textContent=`Step ${stage+1} of 3 · Not saved until you submit`;
    renderReview();
  }
  document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>show(+b.dataset.go)));
  next.addEventListener('click',()=>show(Math.min(2,stage+1)));back.addEventListener('click',()=>show(Math.max(0,stage-1)));
  form.addEventListener('invalid',e=>{const panel=e.target.closest('[data-panel]');if(panel)show(+panel.dataset.panel);let parent=e.target.parentElement;while(parent&&parent!==form){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}},true);
  form.addEventListener('submit',e=>{if(stage!==2){e.preventDefault();show(2);}});
  form.querySelectorAll('[data-search]').forEach(input=>{
    const select=document.getElementById(input.dataset.search);
    input.addEventListener('input',()=>{const q=input.value.toLowerCase();[...select.options].forEach(o=>o.hidden=Boolean(o.value)&&!o.selected&&!o.textContent.toLowerCase().includes(q));});
  });
  const outside=form.elements.outside_required;
  function outsideDetails(){document.getElementById('outside-details').hidden=!outside.checked;}
  outside.addEventListener('change',outsideDetails);outsideDetails();
  const part=document.getElementById('part-choice');
  function partDetails(){const option=part.selectedOptions[0];const link=document.getElementById('selected-drawing');if(part.value){form.elements.pn.readOnly=form.elements.revision.readOnly=true;link.href=option.dataset.url;link.hidden=false;if(!value('description'))form.elements.description.value=option.dataset.description||'';}}
  part.addEventListener('change',partDetails);partDetails();
  // Process edits are summarized after the route editor updates its stored plan.
  form.addEventListener('input',()=>queueMicrotask(renderReview));form.addEventListener('change',()=>queueMicrotask(renderReview));
  form.addEventListener('planchange',renderReview);
  show(0);
})();
