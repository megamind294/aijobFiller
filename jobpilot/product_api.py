"""Shared account-scoped v1 services used by HTTP and MCP transports."""
from flask import Blueprint, abort, current_app, g, jsonify, request

from .api_tokens import TokenStore
from .applications import Applications, fingerprint, job_facts
from .core import read_json
from .workspace import current_home, current_store

PROFILE_FIELDS = ('facts_verified', 'personal', 'summary', 'summary_variants', 'skills', 'languages', 'experience', 'projects', 'education', 'answers', 'document_preferences')
JOB_FIELDS = ('id', 'url', 'title', 'company', 'location', 'description', 'source', 'status', 'created', 'updated')
APPLICATION_FIELDS = ('id', 'job_id', 'state', 'created', 'updated', 'profile_hash', 'job_hash', 'document_hash', 'form_hash', 'document_approved', 'form_approved')


def token_store():
    return current_app.extensions['jobpilot_tokens']


def public_base():
    value = current_app.config.get('MCP_PUBLIC_URL', '').rstrip('/')
    if value:
        from urllib.parse import urlsplit
        p = urlsplit(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('', '/'):
            abort(503, 'MCP_PUBLIC_URL must be a public HTTPS origin.')
        return value
    # Local/test API access may use the current origin, but OAuth metadata requires
    # an explicit canonical public origin to prevent Host-header poisoning.
    return request.root_url.rstrip('/')


def resource_url():
    return public_base() + '/mcp'


def account_for_grant(grant):
    if grant['account_id'] == 'owner':
        from .web_auth import auth_record
        if grant['account_version'] == auth_record()['version']:
            return {'id': 'owner', 'role': 'owner', 'email': '', 'version': grant['account_version']}
        return None
    account = current_app.extensions['jobpilot_accounts'].get(grant['account_id'])
    return account if account and account['version'] == grant['account_version'] else None


def authenticate_request(required=None):
    """Never fall back to browser cookies; bind every bearer request afresh."""
    authorization = request.headers.get('Authorization', '')
    parts = authorization.split()
    grant = token_store().verify(parts[1], required, resource_url()) if len(parts) == 2 and parts[0].lower() == 'bearer' else None
    account = account_for_grant(grant) if grant else None
    if not account:
        response = jsonify(error='invalid_token', message='A valid account token with the required scope is required.')
        response.status_code = 401
        response.headers['WWW-Authenticate'] = 'Bearer resource_metadata="' + public_base() + '/.well-known/oauth-protected-resource/mcp"'
        abort(response)
    g.jobpilot_account = account
    g.jobpilot_token = grant
    from .billing import CreditLedger
    if account.get('role') != 'owner' and not CreditLedger(current_app.config['JOBPILOT_HOME']).has_workspace_access(account['id']):
        response = jsonify(error='payment_required', message='Your free applications are used. A paid plan or purchased credits are required.', billing_url=public_base() + '/billing')
        response.status_code = 402
        abort(response)
    return account


def profile_snapshot():
    path = current_home() / 'profile.json'
    profile = read_json(path) if path.exists() else {}
    return {'version': fingerprint(profile), 'profile': {key: profile[key] for key in PROFILE_FIELDS if key in profile}}


def jobs_snapshot():
    rows = current_store().rows()
    return {'jobs': [{**{k: row.get(k, '') for k in JOB_FIELDS}, 'version': fingerprint(job_facts(row))} for row in rows[:500]]}


def applications_snapshot():
    return {'applications': [{k: row.get(k) for k in APPLICATION_FIELDS} for row in Applications(current_store()).rows()]}


def application_action(action, payload):
    if not isinstance(payload, dict) or payload.get('confirmed') is not True:
        raise ValueError('Explicit confirmation of this exact action is required.')
    text_fields = ('job_id', 'request_key', 'profile_version', 'job_version', 'application_id', 'document_hash', 'form_hash', 'optimization')
    if any(key in payload and (not isinstance(payload[key], str) or len(payload[key]) > 200) for key in text_fields):
        raise ValueError('Invalid action parameters.')
    if 'cover_letter' in payload and not isinstance(payload['cover_letter'], bool):
        raise ValueError('Invalid document preference.')
    service = Applications(current_store())
    if action == 'prepare':
        profile = read_json(current_home() / 'profile.json')
        job = current_store().get(payload.get('job_id', ''))
        if payload.get('profile_version') != fingerprint(profile) or payload.get('job_version') != fingerprint(job_facts(job)):
            raise ValueError('Profile or job snapshot changed. Read the current versions before confirming.')
        run = service.prepare(job['id'], profile, payload.get('request_key', ''), cover_letter=payload.get('cover_letter', True), optimization=payload.get('optimization', 'honest'))
    elif action in ('approve_documents', 'approve_form'):
        identifier = payload.get('application_id', '')
        run = service.get(identifier)
        if payload.get('document_hash') != run['document_hash'] or payload.get('profile_version') != run['profile_hash'] or payload.get('job_version') != run['job_hash']:
            raise ValueError('Application snapshot changed. Read its current hashes before confirming.')
        from .web_applications import approve_and_queue
        if action == 'approve_documents':
            run = approve_and_queue(identifier, payload['document_hash'], 'documents')
        else:
            run = approve_and_queue(identifier, payload.get('form_hash', ''), 'form')
    else:
        raise ValueError('Unsupported action. Application submission requires the reviewed browser workflow.')
    return {'application': {k: run.get(k) for k in APPLICATION_FIELDS}}


def register_product_api(app):
    app.extensions['jobpilot_tokens'] = TokenStore(app.config['JOBPILOT_HOME'])
    api = Blueprint('product_api', __name__, url_prefix='/api/v1')

    @api.get('/profile')
    def profile():
        authenticate_request('profile:read')
        return jsonify(profile_snapshot())

    @api.get('/jobs')
    def jobs():
        authenticate_request('jobs:read')
        return jsonify(jobs_snapshot())

    @api.get('/applications')
    def applications():
        authenticate_request('applications:read')
        return jsonify(applications_snapshot())

    @api.post('/applications/<action>')
    def application_action_route(action):
        authenticate_request('applications:write')
        try:
            return jsonify(application_action(action, request.get_json(silent=True)))
        except (ValueError, KeyError, FileNotFoundError):
            return jsonify(error='invalid_action', message='Action rejected: confirm the current profile, job, and application versions.'), 409

    app.register_blueprint(api)
