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
