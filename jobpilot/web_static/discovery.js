(() => {
  const drawer = document.getElementById('job-detail-drawer');
  if (!drawer || !drawer.showModal) return;
  const content = document.getElementById('job-detail-content');
  let request;
  document.querySelectorAll('[data-job-detail]').forEach(link => {
    link.addEventListener('click', async event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (request) request.abort();
      request = new AbortController();
      content.textContent = 'Loading opportunity details…';
      if (!drawer.open) drawer.showModal();
      try {
        const response = await fetch(link.getAttribute('href') + '?fragment=1', {signal: request.signal});
        if (!response.ok || response.redirected) throw new Error('Could not load this opportunity. Reload the page and try again.');
        content.innerHTML = await response.text();
      } catch (error) {
        if (error.name !== 'AbortError') content.textContent = error.message;
      }
    });
  });
  document.getElementById('close-job-drawer').addEventListener('click', () => drawer.close());
  drawer.addEventListener('close', () => { if (request) request.abort(); });
  const filters = document.getElementById('browse-filters');
  if (filters) filters.addEventListener('submit', () => {
    [...filters.elements].forEach(input => { if (input.name && !input.value) input.disabled = true; });
  });
})();
