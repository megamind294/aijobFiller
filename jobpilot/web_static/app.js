'use strict';
document.querySelectorAll('form').forEach((form) => {
  form.addEventListener('submit', (event) => {
    const file = form.querySelector('input[type="file"]');
    if (file && file.files[0] && file.files[0].size > 5 * 1024 * 1024) {
      event.preventDefault();
      file.setCustomValidity('Choose a file smaller than 5 MiB.');
      file.reportValidity();
      return;
    }
    const button = event.submitter;
    if (button && button.dataset.pending) {
      button.textContent = button.dataset.pending;
      button.disabled = true;
      form.setAttribute('aria-busy', 'true');
    }
  });
});
document.querySelectorAll('input[type="file"]').forEach((input) => {
  input.addEventListener('change', () => input.setCustomValidity(''));
});
document.querySelectorAll('[data-open-dialog]').forEach((button) => {
  button.addEventListener('click', () => document.getElementById(button.dataset.openDialog)?.showModal());
});
document.querySelectorAll('[data-close-dialog]').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog')?.close());
});
