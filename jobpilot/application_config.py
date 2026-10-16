"""One operator adapter configuration for the website and application worker."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from .core import origin, read_json


def load_worker_config(root, overrides=None):
    overrides = overrides or {}
    path = Path(overrides.get('JOBPILOT_APPLICATION_ADAPTERS_FILE') or
                os.environ.get('JOBPILOT_APPLICATION_ADAPTERS_FILE') or Path(root) / 'application-worker.json')
    try:
        config = read_json(path) if path.exists() else {}
    except (OSError, ValueError):
        raise ValueError('Application adapter configuration could not be read. Ask the administrator to check the adapter file.') from None
    if not isinstance(config, dict):
        raise ValueError('Application adapter configuration must be an object.')
    config = dict(config)
    if 'JOBPILOT_APPLICATION_ADAPTERS' in overrides:
        config['adapters'] = overrides['JOBPILOT_APPLICATION_ADAPTERS']
    adapters = config.setdefault('adapters', {})
    if not isinstance(adapters, dict):
        raise ValueError('Application adapters must be a map keyed by exact origin.')
    for key, adapter in adapters.items():
        parsed = urlsplit(key) if isinstance(key, str) else None
        if (not parsed or parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or origin(key) != key or not isinstance(adapter, dict) or
                adapter.get('origin') != key):
            raise ValueError('Application adapters need matching exact HTTP(S) origins.')
    return config
