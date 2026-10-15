"""Request-scoped private data; the original owner keeps their existing files."""
import re
from pathlib import Path

from flask import current_app, g

from .core import Store


def current_home():
    root = Path(current_app.config['JOBPILOT_HOME']).resolve()
    account = getattr(g, 'jobpilot_account', None)
    if account is None or account.get('role') == 'owner':
        return root
    identifier = account.get('id', '')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('Invalid account workspace identifier.')
    home = root / 'accounts' / identifier
    if any(path.is_symlink() for path in (home, *home.parents)):
        raise ValueError('Invalid account workspace path.')
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return home


def current_store():
    home = current_home()
    cached = getattr(g, '_jobpilot_workspace_store', None)
    if cached is None or cached.home != home:
        cached = g._jobpilot_workspace_store = Store(home)
    return cached
