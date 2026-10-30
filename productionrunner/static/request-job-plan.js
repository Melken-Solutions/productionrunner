(() => {
  const root = document.getElementById('request-job-plan');
  if (!root) return;
  const list = document.getElementById('request-plan-steps');
  const addButton = document.getElementById('request-plan-add');
  const saved = JSON.parse(document.getElementById('request-plan-data').textContent);
  const read = node => Object.fromEntries([...node.querySelectorAll('[data-field]')].map(el => [el.dataset.field, el.type === 'checkbox' ? el.checked : el.value]));
  const fill = (node, data) => node.querySelectorAll('[data-field]').forEach(el => {
    if (el.type === 'checkbox') el.checked = data[el.dataset.field] === true;
    else el.value = data[el.dataset.field] || '';
  });
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
    root.querySelectorAll('[data-stage]').forEach(row => { plan[row.dataset.stage] = read(row); });
    document.getElementById('request-plan-json').value = JSON.stringify(plan);
    root.dispatchEvent(new CustomEvent('planchange', {bubbles:true}));
  }
  function add(data = {}) {
    const row = document.getElementById('request-plan-step').content.firstElementChild.cloneNode(true);
    fill(row, data);
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
  root.querySelectorAll('[data-stage]').forEach(row => fill(row, saved?.[row.dataset.stage] || {}));
  document.getElementById('request-plan-dxf').checked = saved?.dxf_required === true;
  (saved?.steps || [{}]).forEach(add);
  addButton.addEventListener('click', () => add());
  root.addEventListener('input', sync); root.addEventListener('change', sync);
  root.closest('form').addEventListener('submit', sync);
  sync();
})();
