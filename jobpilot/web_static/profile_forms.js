"use strict";
document.querySelectorAll('[data-add]').forEach(button => button.addEventListener('click', () => {
  const container = document.querySelector(`[data-repeat="${button.dataset.add}"]`);
  const entry = container.querySelector('[data-entry]').cloneNode(true);
  const indices = [...container.querySelectorAll('[name]')].map(x => Number(x.name.split('_')[1]));
  const index = Math.max(-1, ...indices) + 1;
  entry.querySelectorAll('input,textarea').forEach(input => {
    input.name = input.name.replace(/_\d+_/, `_${index}_`); input.id = input.name; input.value = '';
  });
  entry.querySelectorAll('label').forEach(label => label.htmlFor = label.htmlFor.replace(/_\d+_/, `_${index}_`));
  entry.querySelectorAll('[role="alert"]').forEach(x => x.remove());
  container.append(entry); entry.querySelector('input,textarea').focus();
}));
document.addEventListener('click', event => {
  if (!event.target.matches('[data-remove]')) return;
  const entry = event.target.closest('[data-entry]');
  if (entry.parentElement.querySelectorAll('[data-entry]').length === 1) {
    entry.querySelectorAll('input,textarea').forEach(x => x.value = '');
  } else entry.remove();
});
