document.querySelectorAll('[data-inbox-sync]').forEach(form => {
  form.addEventListener('submit', () => {
    const button = form.querySelector('button');
    button.disabled = true;
    button.textContent = 'Syncing candidate messages…';
    form.setAttribute('aria-busy', 'true');
  });
});
