"""Stateless Streamable HTTP MCP with explicit account OAuth consent (S256)."""
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for

from .api_tokens import SCOPES, scopes
from .product_api import (account_for_grant, application_action, applications_snapshot, authenticate_request,
                          jobs_snapshot, profile_snapshot, public_base, resource_url, token_store)
from .web_auth import authenticated, auth_record

VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26')
READ_TOOLS = {
    'get_profile': ('profile:read', 'Read your source profile and its exact version.', profile_snapshot),
    'list_jobs': ('jobs:read', 'List saved jobs and their exact fact versions.', jobs_snapshot),
    'list_applications': ('applications:read', 'List application package states and review hashes.', applications_snapshot),
}
WRITE_TOOLS = {
    'prepare_application': ('prepare', 'Prepare factual documents for one confirmed job and profile version.'),
    'approve_application_documents': ('approve_documents', 'Approve a reviewed document package with its exact hashes and queue employer-form filling under shared credits and daily limits.'),
    'approve_application_form': ('approve_form', 'Authorize submission of a reviewed employer form with its exact hashes and queue submission under shared credits and daily limits.'),
}


def tool_definitions(allowed):
    tools = []
    for name, (scope, description, _) in READ_TOOLS.items():
        if scope in allowed:
            tools.append({'name': name, 'description': description, 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                          'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}, 'securitySchemes': [{'type': 'oauth2', 'scopes': [scope]}]})
    if 'applications:write' in allowed:
        for name, (action, description) in WRITE_TOOLS.items():
            properties = {'confirmed': {'type': 'boolean', 'const': True}, 'profile_version': {'type': 'string'}, 'job_version': {'type': 'string'}}
            required = ['confirmed', 'profile_version', 'job_version']
            if action == 'prepare':
                properties.update(job_id={'type': 'string'}, request_key={'type': 'string', 'minLength': 1, 'maxLength': 200}, cover_letter={'type': 'boolean'}, optimization={'type': 'string', 'enum': ['off', 'honest']})
                required += ['job_id', 'request_key']
            else:
                properties.update(application_id={'type': 'string'}, document_hash={'type': 'string'})
                required += ['application_id', 'document_hash']
                if action == 'approve_form':
                    properties['form_hash'] = {'type': 'string'}
                    required += ['form_hash']
            tools.append({'name': name, 'description': description + ' Ask the user to confirm this exact snapshot before calling.',
                          'inputSchema': {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
                          'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': action != 'prepare'},
                          'securitySchemes': [{'type': 'oauth2', 'scopes': ['applications:write']}]})
    return tools


def register_mcp(app):
    mcp = Blueprint('mcp', __name__)

    def oauth_ready():
        if not current_app.config.get('MCP_PUBLIC_URL'):
            abort(503, 'Set the canonical HTTPS MCP_PUBLIC_URL before enabling OAuth.')
        return public_base()

    @mcp.get('/.well-known/oauth-protected-resource/mcp')
    def resource_metadata():
        base = oauth_ready()
        return jsonify(resource=base + '/mcp', authorization_servers=[base], scopes_supported=sorted(SCOPES), bearer_methods_supported=['header'])

    @mcp.get('/.well-known/oauth-protected-resource')
    def resource_metadata_root():
        return resource_metadata()

    @mcp.get('/.well-known/oauth-authorization-server')
    def authorization_metadata():
        base = oauth_ready()
        return jsonify(issuer=base, authorization_endpoint=base + '/oauth/authorize', token_endpoint=base + '/oauth/token',
                       registration_endpoint=base + '/oauth/register', revocation_endpoint=base + '/oauth/revoke',
                       response_types_supported=['code'], grant_types_supported=['authorization_code', 'refresh_token'],
                       token_endpoint_auth_methods_supported=['none'], code_challenge_methods_supported=['S256'], scopes_supported=sorted(SCOPES))

    @mcp.post('/oauth/register')
    def register():
        oauth_ready()
        # Bound public DCR storage; only minimal metadata is accepted and saved.
        with token_store().connect() as db:
            if db.execute('SELECT COUNT(*) FROM api_clients WHERE created>?', (time.time() - 3600,)).fetchone()[0] >= 100:
                return jsonify(error='temporarily_unavailable'), 429
        try:
            return jsonify(token_store().register_client(request.get_json(silent=True))), 201
        except (ValueError, TypeError):
            return jsonify(error='invalid_client_metadata'), 400

    @mcp.route('/oauth/authorize', methods=['GET', 'POST'])
    def authorize():
        oauth_ready()
        if not authenticated():
            return redirect(url_for('account_signin', next=request.full_path))
        if request.method == 'POST':
            pending = session.pop('mcp_consent', None)
            if not pending or pending['expires'] <= time.time() or pending['nonce'] != request.form.get('consent_nonce') or pending['account_id'] != g.jobpilot_account['id']:
                abort(400, 'Authorization request expired. Start again from your MCP client.')
            values = pending['values']
        else:
            values = request.args.to_dict()
        try:
            client = token_store().client(values.get('client_id', ''))
            allowed = scopes(values.get('scope', 'profile:read jobs:read applications:read'))
            if (values.get('redirect_uri') not in client['redirect_uris'] or values.get('response_type') != 'code' or
                    values.get('code_challenge_method') != 'S256' or not re.fullmatch(r'[A-Za-z0-9_-]{43}', values.get('code_challenge', '')) or
                    values.get('resource') != resource_url() or len(values.get('state', '')) > 2048):
                raise ValueError('Invalid request.')
        except (ValueError, TypeError):
            abort(400, 'Invalid OAuth authorization request.')
        if request.method == 'GET':
            nonce = secrets.token_urlsafe(24)
            session['mcp_consent'] = {'nonce': nonce, 'values': values, 'expires': time.time() + 600, 'account_id': g.jobpilot_account['id']}
            return render_template('mcp_authorize.html', oauth_client=client, oauth_scopes=allowed, consent_nonce=nonce)
        # Keep the consent POST's redirect same-origin for form-action CSP. The
        # following GET issues the authorization response to the OAuth client.
        session['mcp_authorized_response'] = {'values': values, 'allowed': allowed, 'account_id': g.jobpilot_account['id'],
                                              'allow': request.form.get('decision') == 'allow', 'expires': time.time() + 60}
        return redirect(url_for('mcp.authorization_complete'), code=303)

    @mcp.get('/oauth/complete')
    def authorization_complete():
        oauth_ready()
        pending = session.pop('mcp_authorized_response', None)
        if not authenticated() or not pending or pending['expires'] <= time.time() or pending['account_id'] != g.jobpilot_account['id']:
            abort(400, 'Authorization response expired.')
        values = pending['values']
        result = {'state': values.get('state', '')}
        if pending['allow']:
            account = g.jobpilot_account
            version = auth_record()['version'] if account['id'] == 'owner' else account['version']
            result['code'] = token_store().authorization_code(values['client_id'], values['redirect_uri'], account['id'], version, pending['allowed'], resource_url(), values['code_challenge'])
        else:
            result['error'] = 'access_denied'
        target = urlsplit(values['redirect_uri'])
        return redirect(urlunsplit((target.scheme, target.netloc, target.path, urlencode(parse_qsl(target.query) + list(result.items())), '')))

    @mcp.post('/oauth/token')
    def token():
        oauth_ready()
        values = request.form
        try:
            if values.get('grant_type') == 'authorization_code':
                pair = token_store().exchange(values.get('code', ''), values.get('client_id', ''), values.get('redirect_uri', ''), values.get('code_verifier', ''), values.get('resource', ''))
            elif values.get('grant_type') == 'refresh_token':
                pair = token_store().refresh(values.get('refresh_token', ''), values.get('client_id', ''), values.get('resource', ''))
            else:
                return jsonify(error='unsupported_grant_type'), 400
            # Version revocation applies to refresh grants as well as API access.
            grant = token_store().verify(pair['access_token'], None, resource_url())
            if not account_for_grant(grant):
                token_store().revoke(pair['access_token'])
                raise ValueError('Revoked account.')
            response = jsonify(pair)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
            return response
        except (ValueError, TypeError):
            return jsonify(error='invalid_grant'), 400

    @mcp.post('/oauth/revoke')
    def revoke():
        oauth_ready()
        token_store().revoke(request.form.get('token', ''), request.form.get('client_id', ''))
        return '', 200

    @mcp.get('/connections/chatgpt')
    def connections():
        return render_template('mcp_connections.html', connections=token_store().connections(g.jobpilot_account['id']), mcp_url=resource_url(), oauth_enabled=bool(current_app.config.get('MCP_PUBLIC_URL')))

    @mcp.post('/connections/chatgpt/revoke')
    def connection_revoke():
        token_store().revoke_grant(g.jobpilot_account['id'], request.form.get('grant_id', ''))
        return redirect(url_for('mcp.connections'))

    @mcp.route('/connections/api', methods=['GET', 'POST'])
    def api_tokens():
        raw_token = None
        error = None
        if request.method == 'POST':
            try:
                allowed = scopes(request.form.getlist('scopes'))
                account = g.jobpilot_account
                version = auth_record()['version'] if account['id'] == 'owner' else account['version']
                raw_token = token_store().issue(account['id'], version, allowed, resource_url(), ttl=86400 * 30)
            except ValueError:
                error = 'Select at least one supported permission.'
        response = current_app.make_response(render_template('api_tokens.html', raw_token=raw_token, token_error=error, token_scopes=sorted(SCOPES)))
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @mcp.route('/mcp', methods=['POST', 'GET', 'DELETE'])
    def rpc():
        authenticate_request()
        origin = request.headers.get('Origin')
        if origin and origin != public_base():
            abort(403)
        if request.method != 'POST':
            return '', 405, {'Allow': 'POST'}
        if request.mimetype != 'application/json':
            return jsonify(error='Content-Type must be application/json.'), 415
        if 'application/json' not in request.headers.get('Accept', '') or 'text/event-stream' not in request.headers.get('Accept', ''):
            return jsonify(error='Accept must include application/json and text/event-stream.'), 406
        version = request.headers.get('MCP-Protocol-Version')
        if version and version not in VERSIONS:
            return jsonify(error='Unsupported MCP protocol version.'), 400
        message = request.get_json(silent=True)
        identifier = message.get('id') if isinstance(message, dict) else None

        def error(code, text):
            return jsonify(jsonrpc='2.0', id=identifier, error={'code': code, 'message': text})

        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str) or isinstance(identifier, (dict, list, bool)):
            return error(-32600, 'Invalid JSON-RPC request.')
        method = message['method']
        if 'id' not in message:
            return ('', 202) if method in ('notifications/initialized', 'notifications/cancelled') else error(-32600, 'Unknown notification.')
        params = message.get('params', {})
        if not isinstance(params, dict):
            return error(-32602, 'Invalid parameters.')
        if method == 'initialize':
            requested = params.get('protocolVersion')
            result = {'protocolVersion': requested if requested in VERSIONS else VERSIONS[0], 'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'jobpilot', 'version': '1.0.0'}}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': tool_definitions(g.jobpilot_token['scopes'].split())}
        elif method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments', {})
            allowed_tools = {tool['name']: tool for tool in tool_definitions(g.jobpilot_token['scopes'].split())}
            if not isinstance(name, str) or name not in allowed_tools or not isinstance(arguments, dict):
                return error(-32602, 'Unknown tool, insufficient scope, or invalid arguments.')
            schema = allowed_tools[name]['inputSchema']
            if set(arguments) - set(schema['properties']) or set(schema.get('required', [])) - set(arguments):
                return error(-32602, 'Arguments must match the tool schema.')
            try:
                data = READ_TOOLS[name][2]() if name in READ_TOOLS else application_action(WRITE_TOOLS[name][0], arguments)
                result = {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'structuredContent': data, 'isError': False}
            except (ValueError, KeyError, FileNotFoundError, TypeError):
                result = {'content': [{'type': 'text', 'text': 'Action rejected. Review the current source versions and confirm the exact action.'}], 'isError': True}
        else:
            return error(-32601, 'Method not found.')
        return jsonify(jsonrpc='2.0', id=identifier, result=result)

    app.register_blueprint(mcp)
