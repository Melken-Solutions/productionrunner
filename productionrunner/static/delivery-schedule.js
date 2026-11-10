(() => {
  document.querySelectorAll('.delivery-schedule').forEach(root => {
    const form=root.closest('form'), mode=root.querySelector('[name=delivery_mode]');
    const rows=root.querySelector('[data-shipment-rows]'), fields=root.querySelector('[data-shipment-fields]');
    const totalInput=form.querySelector('[name=quantity]'),deadline=form.querySelector('[name=required_date]');
    const add=()=>{rows.append(root.querySelector('template').content.cloneNode(true));update();};
    function update(){
      const staged=mode.value==='staged';fields.hidden=!staged;
      const total=Number(totalInput ? totalInput.value : root.dataset.total);
      let scheduled=0,previousDate='';
      rows.querySelectorAll('.shipment-row').forEach(row=>{
        const q=row.querySelector('[name=shipment_quantity]'),d=row.querySelector('[name=shipment_date]');
        q.disabled=d.disabled=!staged;q.required=staged&&Boolean(d.value);d.required=staged&&Boolean(q.value);
        q.setCustomValidity('');d.setCustomValidity('');
        if(deadline)d.max=deadline.value;
        if(staged&&d.value&&previousDate>d.value)d.setCustomValidity('Put shipments in ship-date order, earliest first.');
        if(d.value)previousDate=d.value;
        scheduled+=Number(q.value)||0;
      });
      const difference=total-scheduled;
      root.querySelector('[data-schedule-balance]').textContent=`${scheduled} scheduled / ${total} units · ${difference>=0?difference+' remaining to schedule':Math.abs(difference)+' over the order quantity'}`;
      const first=rows.querySelector('[name=shipment_quantity]');
      if(first&&staged&&difference<0)first.setCustomValidity('Scheduled shipments exceed the order quantity.');
    }
    root.querySelector('[data-add-shipment]').addEventListener('click',add);
    rows.addEventListener('click',e=>{if(e.target.closest('[data-remove-shipment]')){e.target.closest('.shipment-row').remove();update();form.dispatchEvent(new Event('input',{bubbles:true}));}});
    root.addEventListener('input',update);mode.addEventListener('change',()=>{if(mode.value==='staged'&&!rows.children.length)add();update();});
    totalInput?.addEventListener('input',update);deadline?.addEventListener('input',update);
    if(mode.value==='staged'&&!rows.children.length)add();update();
  });
})();
