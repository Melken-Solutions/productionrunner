"use strict";
const draft = document.getElementById('job-plan-edit');
const release = document.getElementById('plan-release');
if (draft && release) {
  const markDirty = () => {
    release.disabled = true;
    document.getElementById('plan-unsaved').hidden = false;
  };
  draft.addEventListener('input', markDirty);
  draft.addEventListener('change', markDirty);
}
