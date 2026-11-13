"use strict";
// No completion is queued offline. Existing server version checks remain authoritative.
document.querySelectorAll("[data-complete-form]").forEach(form => {
  form.addEventListener("submit", event => {
    if (form.dataset.submitting === "true") { event.preventDefault(); return; }
    if (!navigator.onLine) {
      event.preventDefault();
      form.querySelector("[data-submit-status]").textContent = "Offline — nothing recorded. Reconnect, then reopen the traveler to check its live status.";
      return;
    }
    form.dataset.submitting = "true";
    form.querySelector("button").disabled = true;
    form.querySelector("[data-submit-status]").textContent = "Saving… Wait for the updated traveler before continuing.";
  });
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll("[data-complete-form]").forEach(form => {
    delete form.dataset.submitting;
    form.querySelector("button").disabled = false;
    form.querySelector("[data-submit-status]").textContent = "";
  });
});

// Keep routine confirmations compact; reveal evidence only when it is needed.
document.querySelectorAll('[name=verification_result]').forEach(select => {
  const details=select.closest('fieldset').querySelector('[data-verification-details]');
  const field=details.querySelector('textarea');
  const update=()=>{const required=!!select.value&&select.value!=='checks_passed';field.required=required;details.open=required;};
  select.addEventListener('change',update);update();
});
