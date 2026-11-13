"use strict";
document.querySelectorAll('.vendor-choice').forEach(group => {
  const vendor = group.querySelector('[data-vendor]');
  const service = group.querySelector('[data-service]');
  function refresh() {
    for (const option of service.options) {
      const unavailable = !!option.value && option.dataset.vendorName !== vendor.value;
      option.hidden = unavailable;
      option.disabled = unavailable;
    }
    if (service.selectedOptions[0]?.disabled) service.value = '';
  }
  vendor.addEventListener('change', () => { service.value = ''; refresh(); });
  refresh();
});
