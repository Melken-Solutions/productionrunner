(() => {
  const root = document.getElementById('request-job-plan');
  if (!root) return;
  const list = document.getElementById('request-plan-steps');
  const addButton = document.getElementById('request-plan-add');
  let templateId = null;
  const library = JSON.parse(document.getElementById('operation-library-data').textContent);
  const saved = JSON.parse(document.getElementById('request-plan-data').textContent);
  const read = node => ({...Object.fromEntries([...node.querySelectorAll('[data-field]')].map(el => [el.dataset.field, el.type === 'checkbox' ? el.checked : el.value])), ...(node.dataset.templateKey ? {operation_template:node.dataset.templateKey, operation_template_version:Number(node.dataset.templateVersion)} : {})});
  const fill = (node, data) => {
    node.dataset.templateKey=data.operation_template || ''; node.dataset.templateVersion=data.operation_template_version || 0;
    node.querySelectorAll('[data-field]').forEach(el => {
    if (el.type === 'checkbox') el.checked = data[el.dataset.field] === true;
    else el.value = data[el.dataset.field] || '';
  });
    node.dispatchEvent(new Event('templatefilled'));
    const picker=node.querySelector('[data-operation-template]');if(picker && picker.options.length>1)picker.value=node.dataset.templateKey;
  };
  function templatePicker(node, kind) {
    const select=node.querySelector('[data-operation-template]');
    const options=library.filter(t=>t.kind===kind);
    const station=node.querySelector('[data-field=station_id]');
    function refresh(){
      const key=node.dataset.templateKey || '';
      select.replaceChildren(new Option('Choose a standard operation or use custom instructions',''));
      options.filter(t=>!station.value || !t.station_ids?.length || t.station_ids.map(String).includes(station.value)).forEach(t=>select.add(new Option(t.name,t.key)));
      if(key && ![...select.options].some(o=>o.value===key))select.add(new Option('Saved operation — review station compatibility',key));
      select.value=key;
      const note=node.querySelector('[data-template-note]');
      const current=options.find(t=>t.key===key);
      if(note)note.textContent=key && (!current || current.version!==Number(node.dataset.templateVersion))?'Saved instructions retained. The library changed; review before replacing.':key && station.value && current?.station_ids?.length && !current.station_ids.map(String).includes(station.value)?'Station changed. Review whether these saved instructions apply here.':'Choose the station, then the operation. Instructions and checks fill automatically; review drawing-specific requirements.';
    }
    station.addEventListener('change',refresh);node.addEventListener('templatefilled',refresh);refresh();
    select.addEventListener('change',()=>{
      const chosen=options.find(t=>t.key===select.value);
      if (!chosen) {node.dataset.templateKey='';sync();return;}
      const previous=read(node);
      if ((previous.instructions || previous.criteria) && !window.confirm('Replace this step’s instructions and checks with the selected template? Review the result before saving.')) {select.value=node.dataset.templateKey || '';return;}
      fill(node,{...previous,...chosen,station_id:previous.station_id || chosen.station_id || '',outside:false,operation_template:chosen.key,operation_template_version:chosen.version});
      refresh();
      const outside=node.querySelector('[data-field=outside]');if(outside)outside.dispatchEvent(new Event('change'));
      if(chosen.dxf_required)document.getElementById('request-plan-dxf').checked=true;
      sync();
    });
  }
  function sync() {
    // Keep vendor work after internal production, including when adding a later step.
    [...list.children].filter(row => row.querySelector('[data-field="outside"]').checked).forEach(row => { if (list.lastElementChild !== row) list.append(row); });
    const steps = [...list.children];
    steps.forEach((row, i) => {
      row.querySelector('[data-number]').textContent = i + 1;
      row.querySelector('[data-action="up"]').disabled = i === 0 || row.querySelector('[data-field="outside"]').checked;
      row.querySelector('[data-action="down"]').disabled = i === steps.length - 1 || steps[i + 1].querySelector('[data-field="outside"]').checked;
    });
    addButton.disabled = steps.length >= 20;
    const plan = {steps: steps.map(read), dxf_required: document.getElementById('request-plan-dxf').checked};
    if (templateId) plan.template_id = templateId;
    root.querySelectorAll('[data-stage]').forEach(row => { plan[row.dataset.stage] = read(row); });
    document.getElementById('request-plan-json').value = JSON.stringify(plan);
    root.dispatchEvent(new CustomEvent('planchange', {bubbles:true}));
  }
  function add(data = {}) {
    const row = document.getElementById('request-plan-step').content.firstElementChild.cloneNode(true);
    fill(row, data);
    templatePicker(row, 'production');
    const outside = row.querySelector('[data-field="outside"]');
    const station = row.querySelector('[data-field="station_id"]');
    const first = row.querySelector('[data-field="first_piece"]');
    station.addEventListener('change', () => {
      const name = row.querySelector('[data-field="name"]');
      if (!name.value.trim() && station.value) name.value = station.selectedOptions[0].textContent.trim();
    });
    function kind() {
      station.disabled = first.disabled = outside.checked;
      if (outside.checked) { station.value = ''; first.checked = false; }
    }
    outside.addEventListener('change', kind); kind();
    row.addEventListener('click', e => {
      const action = e.target.dataset.action;
      if (action === 'remove') row.remove();
      if (action === 'up' && row.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
      if (action === 'down' && row.nextElementSibling) list.insertBefore(row.nextElementSibling, row);
      sync();
    });
    list.append(row); sync();
  }
  root.addEventListener('loadtemplate', event => {
    const plan = event.detail || {};
    templateId = plan.template_id || null;
    list.replaceChildren();
    root.querySelectorAll('[data-stage]').forEach(row => fill(row, plan[row.dataset.stage] || {}));
    document.getElementById('request-plan-dxf').checked = plan.dxf_required === true;
    (plan.steps || [{}]).forEach(add);
    sync();
  });
  templateId = saved?.template_id || null;
  root.querySelectorAll('[data-stage]').forEach(row => {
    const defaults=library.filter(t=>t.kind===row.dataset.stage);
    const starter=!saved && defaults.length===1 ? {...defaults[0],operation_template:defaults[0].key,operation_template_version:defaults[0].version} : {};
    fill(row, saved?.[row.dataset.stage] || starter);templatePicker(row,row.dataset.stage);
  });
  document.getElementById('request-plan-dxf').checked = saved?.dxf_required === true;
  (saved?.steps || [{}]).forEach(add);
  addButton.addEventListener('click', () => add());
  root.addEventListener('input', sync); root.addEventListener('change', sync);
  root.closest('form').addEventListener('submit', sync);
  sync();
})();
