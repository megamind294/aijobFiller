(() => {
  const form = document.getElementById('assistant-form');
  if (!form) return;
  const messages = document.getElementById('assistant-messages');
  const error = document.getElementById('assistant-error');
  const input = document.getElementById('assistant-question');
  const button = form.querySelector('button[type="submit"]');
  const pending = document.getElementById('assistant-pending');
  let loaded = false;
  const append = (role, text) => {
    const item = document.createElement('div');
    item.className = `assistant-message ${role === 'user' ? 'from-user' : 'from-assistant'}`;
    const label = document.createElement('strong');
    label.textContent = role === 'user' ? 'You' : 'JobPilot';
    const body = document.createElement('p');
    body.textContent = text;
    item.append(label, body);
    messages.append(item);
    messages.scrollTop = messages.scrollHeight;
  };
  document.querySelectorAll('[data-open-dialog="assistant-dialog"]').forEach((opener) => {
    opener.addEventListener('click', async () => {
      if (loaded) return;
      loaded = true;
      try {
        const response = await fetch('/assistant/history');
        if (!response.ok || response.redirected) throw new Error('Sign in again to use the assistant.');
        const data = await response.json();
        if (data.messages.length) {
          messages.replaceChildren();
          data.messages.forEach((item) => append(item.role, item.content));
        }
      } catch (problem) {
        loaded = false;
        error.textContent = problem.message;
        error.hidden = false;
      }
    });
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || button.disabled) return;
    button.disabled = true;
    pending.textContent = 'Thinking…';
    error.hidden = true;
    form.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(form.action, {method:'POST',
        headers:{'Content-Type':'application/json', 'X-CSRFToken':form.elements.csrf_token.value},
        body:JSON.stringify({message})});
      if (response.redirected) throw new Error('Your session or workspace access changed. Reload the page to continue.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'The assistant could not answer. Try again.');
      if (!messages.querySelector('.assistant-message')) messages.replaceChildren();
      append('user', message);
      append('assistant', data.reply);
      input.value = '';
    } catch (problem) {
      error.textContent = problem.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
      pending.textContent = '';
      form.removeAttribute('aria-busy');
    }
  });
})();
