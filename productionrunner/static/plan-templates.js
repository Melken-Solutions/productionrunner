(() => {
  const form = document.getElementById('planner-form');
  const choice = document.getElementById('part-choice');
  const root = document.getElementById('request-job-plan');
  const status = document.getElementById('part-template-status');
  if (!form || !choice || !root) return;
  const materialGroup = form.querySelector('[name="material_id"]')?.closest('fieldset');
  function busy(value) { loading=value; root.inert=value; if(materialGroup)materialGroup.inert=value; }
  let generation = 0, dirty = form.dataset.editing === 'yes', loading = false;
  // Only user edits count; programmatic rendering must not mark a plan dirty.
  root.addEventListener('input', () => { dirty = true; });
  root.addEventListener('click', e => { if (e.target.closest('button')) dirty = true; });
  form.querySelector('[name="material_id"]')?.addEventListener('change', () => { dirty = true; });
  function set(name, value) {
    const el = form.elements.namedItem(name);
    if (el) { el.value = value || ''; el.dispatchEvent(new Event('change', {bubbles:true})); }
  }
  function clearDefaults() {
    root.dispatchEvent(new CustomEvent('loadtemplate', {detail:null}));
    set('material_id',''); set('material_notes',''); set('planned_sheets','');
    const outside = form.elements.namedItem('outside_required');
    outside.checked = false;
    for (const key of ['vendor','service','instructions','inspection_requirements','due_date','reference']) set('outside_'+key,'');
    outside.dispatchEvent(new Event('change',{bubbles:true}));
  }
  async function load() {
    const token = ++generation;
    if (dirty && !window.confirm('Replace the current manufacturing plan and material with defaults for this part? Job-specific dates and vendor references will be cleared.')) {
      // Explicitly retain a one-off plan, but never attribute it to a different PN.
      const plan = JSON.parse(document.getElementById('request-plan-json').value);
      delete plan.template_id;
      root.dispatchEvent(new CustomEvent('loadtemplate',{detail:plan}));
      status.textContent = 'Current plan retained as a one-off plan. Review every step for the selected part.';
      busy(false);
      return;
    }
    clearDefaults(); dirty = false;
    const option = choice.selectedOptions[0];
    if (!choice.value) {status.textContent='Enter the part and build its Job Plan.'; busy(false); return;}
    busy(true); status.textContent='Looking for an approved manufacturing plan…';
    try {
      const response=await fetch(option.dataset.templateUrl,{headers:{Accept:'application/json'}});
      const body=await response.json();
      if (token!==generation) return;
      if (!response.ok) throw new Error(body.error || 'Template unavailable. Review the part template before continuing.');
      if (!body.template) {status.textContent='No approved template for this PN/revision yet. Build the plan, then save it as a template after approval.'; return;}
      const t=body.template;
      root.dispatchEvent(new CustomEvent('loadtemplate',{detail:{...t.plan,template_id:t.id}}));
      set('material_id',t.material?.material_id); set('material_notes',t.material?.notes);
      const outside=form.elements.namedItem('outside_required');
      outside.checked=Boolean(t.outside);
      for (const key of ['vendor','service','instructions','inspection_requirements']) set('outside_'+key,t.outside?.[key]);
      outside.dispatchEvent(new Event('change',{bubbles:true}));
      status.textContent='Approved template revision '+t.revision+' loaded. Review the plan and enter job-specific details'+(t.outside?', including the outside return date.':'.');
      dirty=false;
    } catch (error) {
      if (token===generation) status.textContent=error.message || 'Could not load the template. Try selecting the part again.';
    } finally {if (token===generation) busy(false);}
  }
  choice.addEventListener('change', load);
  form.addEventListener('submit', e => {if (loading) {e.preventDefault();status.textContent='Wait for the manufacturing plan to finish loading before saving.';}});
  if (form.dataset.editing!=='yes' && choice.value) choice.dispatchEvent(new Event('change',{bubbles:true}));
})();
