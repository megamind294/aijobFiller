"""Private browser interface; no scheduled discovery or application submission."""
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, g, render_template, request
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
        MAX_CONTENT_LENGTH=5 * 1024 * 1024, MAX_FORM_PARTS=500,
        MAX_FORM_MEMORY_SIZE=512 * 1024,
        WTF_CSRF_SSL_STRICT=False,
        MCP_PUBLIC_URL=os.environ.get('MCP_PUBLIC_URL') or (
            'https://' + os.environ['JOBPILOT_WEB_HOST'] if os.environ.get('JOBPILOT_WEB_HOST') else ''),
        BILLING_PUBLIC_URL=os.environ.get('BILLING_PUBLIC_URL') or (
            'https://' + os.environ['JOBPILOT_WEB_HOST'] if os.environ.get('JOBPILOT_WEB_HOST') else ''),
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

    @app.context_processor
    def workspace_context():
        from .billing import CreditLedger
        account = getattr(g, 'jobpilot_account', {'id': 'owner', 'role': 'owner', 'email': ''})
        credits = {'available': 0, 'reserved': 0}
        trial = {'eligible': False, 'remaining': 0, 'reserved': 0}
        if account.get('role') != 'owner':
            ledger = CreditLedger(home)
            credits = ledger.balance(account['id'])
            trial = ledger.trial_status(account['id'])
        initials = 'JP' if account.get('role') == 'owner' else (account.get('email', '')[:2].upper() or 'ME')
        return {'workspace_account': account, 'workspace_credits': credits, 'workspace_trial': trial, 'workspace_initials': initials}

    @app.before_request
    def verify_browser_origin():
        from .web_auth import PROTOCOL_ENDPOINTS
        if request.endpoint in PROTOCOL_ENDPOINTS:
            return None
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

    csrf = CSRFProtect(app)
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
        from .workspace import current_home, current_store
        store = current_store()
        rows = store.rows()
        ready = False
        try:
            validate_profile(read_json(current_home() / 'profile.json'))
            ready = True
        except (ValueError, FileNotFoundError, TypeError):
            pass
        from .discovery_catalog import catalog_rows, explain_match
        try:
            profile = read_json(current_home() / 'profile.json')
        except (ValueError, FileNotFoundError):
            profile = {}
        matches = catalog_rows(store)[:6]
        for job in matches:
            job['match'] = explain_match(job, profile)
        return render_template('dashboard.html', jobs=rows[:5], counts=Counter(j['status'] for j in rows),
                               total=len(rows), profile_ready=ready, matches=matches)

    @app.get('/privacy')
    def public_privacy():
        return render_template('public_info.html', privacy=True)

    @app.get('/favicon.ico')
    def favicon():
        return '', 204

    register_jobs(app)
    register_settings(app)
    register_resume_uploads(app)
    from .profile_vault import register_profile_vault
    from .web_discovery import register_discovery
    from .web_inbox import register_inbox
    register_profile_vault(app)
    register_discovery(app)
    register_inbox(app)
    from .web_accounts import register_accounts
    register_accounts(app)
    from .web_billing import register_billing
    from .product_api import register_product_api
    from .mcp_server import register_mcp
    from .web_applications import register_applications
    from .web_automation import register_automation
    register_billing(app)
    register_product_api(app)
    register_mcp(app)
    register_applications(app)
    register_automation(app)
    from .workspace_pages import register_workspace_pages
    register_workspace_pages(app)
    from .customer_admin import register_customer_admin
    register_customer_admin(app)
    from .assistant import register_assistant
    register_assistant(app)
    from .web_auth import PROTOCOL_ENDPOINTS
    for endpoint in PROTOCOL_ENDPOINTS:
        view = app.view_functions.get(endpoint)
        if view is not None:
            csrf.exempt(view)
    return app
