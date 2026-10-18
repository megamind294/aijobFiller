"use strict";
document.querySelectorAll('[data-add]').forEach(button => button.addEventListener('click', () => {
  const container = document.querySelector(`[data-repeat="${button.dataset.add}"]`);
  const entry = container.querySelector('[data-entry]').cloneNode(true);
  const indices = [...container.querySelectorAll('[name]')].map(x => Number(x.name.split('_')[1]));
  const index = Math.max(-1, ...indices) + 1;
  entry.querySelectorAll('input,textarea').forEach(input => {
    input.name = input.name.replace(/_\d+_/, `_${index}_`); input.id = input.name; input.value = '';
    input.removeAttribute('aria-invalid');
  });
  entry.querySelectorAll('label').forEach(label => label.htmlFor = label.htmlFor.replace(/_\d+_/, `_${index}_`));
  entry.querySelectorAll('[role="alert"]').forEach(x => x.remove());
  container.append(entry); entry.querySelector('input,textarea').focus();
}));
document.addEventListener('click', event => {
  if (!event.target.matches('[data-remove]')) return;
  const entry = event.target.closest('[data-entry]');
  if (entry.parentElement.querySelectorAll('[data-entry]').length === 1) {
    entry.querySelectorAll('input,textarea').forEach(x => { x.value = ''; x.removeAttribute('aria-invalid'); });
    entry.querySelectorAll('[role="alert"]').forEach(x => x.remove());
  } else entry.remove();
});

// The text fields remain a usable fallback when JavaScript is unavailable.
const sectionList = document.querySelector('[data-document-sections]');
const sectionValue = document.getElementById('preference_sections');
function syncDocumentSections() {
  const rows = [...sectionList.children];
  sectionValue.value = rows.filter(row => row.querySelector('[data-section-visible]').checked).map(row => row.dataset.section).join(',');
  rows.forEach((row,index) => {row.querySelector('[data-section-move="up"]').disabled = index === 0; row.querySelector('[data-section-move="down"]').disabled = index === rows.length - 1;});
}
if (sectionList && sectionValue) {
  sectionList.addEventListener('change', syncDocumentSections);
  sectionList.addEventListener('click', event => {
    const button = event.target.closest('[data-section-move]'); if (!button) return;
    const row = button.closest('[data-section]');
    if (button.dataset.sectionMove === 'up' && row.previousElementSibling) sectionList.insertBefore(row, row.previousElementSibling);
    else if (button.dataset.sectionMove === 'down' && row.nextElementSibling) sectionList.insertBefore(row.nextElementSibling, row);
    syncDocumentSections(); button.focus();
  });
  sectionValue.addEventListener('input', () => {
    const names = sectionValue.value.split(',').map(x => x.trim());
    [...sectionList.children].forEach(row => row.querySelector('[data-section-visible]').checked = names.includes(row.dataset.section));
    names.forEach(name => {const row = [...sectionList.children].find(x => x.dataset.section === name); if (row) sectionList.append(row);});
  });
}
const headerValue = document.getElementById('preference_header_fields');
if (headerValue) {
  document.querySelectorAll('[data-header-field]').forEach(input => input.addEventListener('change', () => {
    headerValue.value = [...document.querySelectorAll('[data-header-field]:checked')].map(x => x.dataset.headerField).join(',');
  }));
  headerValue.addEventListener('input', () => {
    const names = headerValue.value.split(',').map(x => x.trim());
    document.querySelectorAll('[data-header-field]').forEach(input => input.checked = names.includes(input.dataset.headerField));
  });
}
