"""Private browser interface; no scheduled discovery or application submission."""
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .core import Store, read_json, validate_profile
from .web_auth import init_auth
from .web_jobs import register_jobs
from .web_settings import register_settings
from .resume_uploads import register_resume_uploads


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
        WTF_CSRF_SSL_STRICT=False,
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

    @app.before_request
    def verify_browser_origin():
        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return None
        # Privacy browsers may omit Referer. Check Origin when available;
        # signed session-bound CSRF tokens remain mandatory in all cases.
        origin = request.headers.get('Origin') or request.referrer
        if origin:
            def origin_parts(value):
                parsed = urlsplit(value)
                return (parsed.scheme, parsed.hostname,
                        parsed.port or (443 if parsed.scheme == 'https' else 80))
            try:
                valid_origin = origin_parts(origin) == origin_parts(request.host_url)
            except ValueError:
                valid_origin = False
            if not valid_origin:
                abort(400)
        if request.headers.get('Sec-Fetch-Site') == 'cross-site':
            abort(400)

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

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        # The framework's fixed reason contains no token or submitted fields.
        app.logger.warning('CSRF verification rejected a request: %s', error.description)
        return render_template('error.html', code=400,
                               message='This form has expired or its session is missing. Open the login page again and retry.'), 400

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

    @app.get('/favicon.ico')
    def favicon():
        return '', 204

    register_jobs(app)
    register_settings(app)
    register_resume_uploads(app)
    return app
