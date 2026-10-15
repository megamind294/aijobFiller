"""Private browser interface; no scheduled discovery or application submission."""
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path

from flask import Flask, render_template
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .core import Store, read_json, validate_profile
from .web_auth import init_auth
from .web_jobs import register_jobs
from .web_settings import register_settings


def create_app(config=None):
    app = Flask(__name__, template_folder='web_templates', static_folder='web_static')
    app.config.update(
        JOBPILOT_HOME=os.environ.get('JOBPILOT_HOME', '/var/lib/jobpilot'),
        SECRET_KEY=os.environ.get('JOBPILOT_WEB_SECRET'),
        SESSION_COOKIE_NAME='jobpilot_session', SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024, MAX_FORM_PARTS=40,
        MAX_FORM_MEMORY_SIZE=512 * 1024,
    )
    if config:
        app.config.update(config)
    if not app.secret_key:
        raise RuntimeError('JOBPILOT_WEB_SECRET is required.')
    if os.environ.get('JOBPILOT_WEB_HOST'):
        app.config['TRUSTED_HOSTS'] = [os.environ['JOBPILOT_WEB_HOST'], 'localhost', '127.0.0.1']
    home = Path(app.config['JOBPILOT_HOME']).resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    app.config['JOBPILOT_HOME'] = str(home)
    app.extensions['jobpilot_store'] = Store(home)
    init_auth(app)
    CSRFProtect(app)
    # Production is loopback-only behind exactly one Caddy proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0)

    @app.after_request
    def private_headers(response):
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        })
        return response

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def request_error(error):
        messages = {400: 'The request could not be verified. Reload the page and try again.',
                    404: 'That item could not be found.', 413: 'The file or form exceeds the 5 MiB limit.'}
        return render_template('error.html', code=error.code, message=messages[error.code]), error.code

    @app.get('/')
    def dashboard():
        store = app.extensions['jobpilot_store']
        rows = store.rows()
        ready = False
        try:
            validate_profile(read_json(home / 'profile.json'))
            ready = True
        except (ValueError, FileNotFoundError, TypeError):
            pass
        return render_template('dashboard.html', jobs=rows[:5], counts=Counter(j['status'] for j in rows),
                               total=len(rows), profile_ready=ready)

    register_jobs(app)
    register_settings(app)
    return app
