"use strict";
(() => {
  const form = document.getElementById('sign-in-form');
  if (!form) return;
  const key = 'productionrunner.sign-in-preferences';
  const tenant = form.elements.tenant;
  const username = form.elements.username;
  const method = form.elements.method;
  const remember = document.getElementById('remember-sign-in');
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (saved && typeof saved.tenant === 'string' && typeof saved.username === 'string') {
      if (!tenant.value) tenant.value = saved.tenant;
      if (!username.value) username.value = saved.username;
      if (saved.method === 'pin' || saved.method === 'password') method.value = saved.method;
      remember.checked = true;
    }
  } catch (_) { /* Sign-in works when browser storage is unavailable. */ }
  function updateMethod() {
    const usePin = method.value === 'pin';
    form.querySelector('[data-password-field]').hidden = usePin;
    form.querySelector('[data-pin-field]').hidden = !usePin;
    form.elements.password.disabled = usePin;
    form.elements.password.required = !usePin;
    form.elements.pin.disabled = !usePin;
    form.elements.pin.required = usePin;
    form.elements.pin.value = '';
  }
  function forget() {
    try { localStorage.removeItem(key); } catch (_) {}
  }
  remember.addEventListener('change', () => { if (!remember.checked) forget(); });
  method.addEventListener('change', updateMethod);
  window.addEventListener('pageshow', () => { form.elements.pin.value = ''; });
  form.addEventListener('submit', () => {
    if (!remember.checked) { forget(); return; }
    try {
      localStorage.setItem(key, JSON.stringify({tenant: tenant.value.trim(), username: username.value.trim(), method: method.value}));
    } catch (_) { /* Storage preferences never prevent authentication. */ }
  });
  updateMethod();
})();
