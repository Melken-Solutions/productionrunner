const choice = document.getElementById('part-choice');
const pn = document.querySelector('[name="pn"]');
const revision = document.querySelector('[name="revision"]');
const drawingLink = document.getElementById('selected-drawing');
choice.addEventListener('change', () => {
  const option = choice.selectedOptions[0];
  pn.readOnly = revision.readOnly = Boolean(choice.value);
  if (choice.value) {
    pn.value = option.dataset.pn;
    revision.value = option.dataset.revision;
    drawingLink.href = option.dataset.url;
  } else {
    pn.value = revision.value = '';
    drawingLink.removeAttribute('href');
  }
  drawingLink.hidden = !choice.value;
});
