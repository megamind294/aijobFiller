import tempfile
import unittest
from pathlib import Path


class TokenTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.api_tokens import TokenStore
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tokens = TokenStore(Path(self.temp.name))

    def test_hash_scope_expiry_and_grant_revocation(self):
        token = self.tokens.issue('a' * 32, 'version', ['profile:read'], 'https://example.com/mcp')
        self.assertEqual(self.tokens.verify(token, 'profile:read', 'https://example.com/mcp')['account_id'], 'a' * 32)
        self.assertIsNone(self.tokens.verify(token, 'jobs:read', 'https://example.com/mcp'))
        self.assertIsNone(self.tokens.verify(token, 'profile:read', 'https://other.com/mcp'))
        self.assertNotIn(token.encode(), self.tokens.path.read_bytes())
        self.tokens.revoke(token)
        self.assertIsNone(self.tokens.verify(token, 'profile:read', 'https://example.com/mcp'))

    def test_code_pkce_single_use_and_refresh_rotation(self):
        from jobpilot.api_tokens import pkce_challenge
        verifier = 'v' * 43
        client = self.tokens.register_client({'client_name': 'Test', 'redirect_uris': ['https://client.example/callback']})
        code = self.tokens.authorization_code(client['client_id'], client['redirect_uris'][0], 'a' * 32, 'version', ['profile:read'], 'https://example.com/mcp', pkce_challenge(verifier))
        with self.assertRaises(ValueError):
            self.tokens.exchange(code, client['client_id'], client['redirect_uris'][0], 'wrong', 'https://example.com/mcp')
        pair = self.tokens.exchange(code, client['client_id'], client['redirect_uris'][0], verifier, 'https://example.com/mcp')
        with self.assertRaises(ValueError):
            self.tokens.exchange(code, client['client_id'], client['redirect_uris'][0], verifier, 'https://example.com/mcp')
        next_pair = self.tokens.refresh(pair['refresh_token'], client['client_id'], 'https://example.com/mcp')
        with self.assertRaises(ValueError):
            self.tokens.refresh(pair['refresh_token'], client['client_id'], 'https://example.com/mcp')
        self.assertIsNone(self.tokens.verify(next_pair['access_token'], 'profile:read', 'https://example.com/mcp'))
        self.tokens.revoke(next_pair['refresh_token'])
        self.assertIsNone(self.tokens.verify(next_pair['access_token'], 'profile:read', 'https://example.com/mcp'))

    def test_redirects_reject_credentials_fragments_local_network_and_http(self):
        for uri in ['http://example.com/cb', 'https://user:pass@example.com/cb', 'https://example.com/cb#x', 'https://127.0.0.1/cb', 'javascript:alert(1)']:
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                self.tokens.register_client({'redirect_uris': [uri]})


class TransportTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from jobpilot.accounts import Accounts
        from jobpilot.core import write_json
        from jobpilot.product_api import register_product_api
        from jobpilot.mcp_server import register_mcp
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.app = Flask('test', template_folder=str(Path(__file__).parents[1] / 'jobpilot' / 'web_templates'))
        self.app.config.update(SECRET_KEY='test', TESTING=True, JOBPILOT_HOME=str(self.home), MCP_PUBLIC_URL='https://example.com', AUTH_FILE=str(self.home / 'auth.json'))
        write_json(self.home / 'auth.json', {'version': 'v1'})
        self.app.extensions['jobpilot_accounts'] = Accounts(self.home)
        register_product_api(self.app)
        register_mcp(self.app)
        self.client = self.app.test_client()

    def token(self, account=None, allowed=None):
        return self.app.extensions['jobpilot_tokens'].issue(account['id'] if account else 'owner', account['version'] if account else 'v1', allowed or ['profile:read'], 'https://example.com/mcp')

    def test_api_bearer_scope_isolation_and_no_cookie_fallback(self):
        from jobpilot.core import write_json
        account = self.app.extensions['jobpilot_accounts'].identity('email', 'alice@example.com', verified=True)
        from jobpilot.billing import CreditLedger
        CreditLedger(self.home).grant(account['id'], 'paid-isolation-fixture', 1, 'wallet')
        home = self.home / 'accounts' / account['id']
        write_json(self.home / 'profile.json', {'summary': 'owner secret'})
        write_json(home / 'profile.json', {'summary': 'alice', 'api_key': 'hidden'})
        self.assertEqual(self.client.get('/api/v1/profile').status_code, 401)
        response = self.client.get('/api/v1/profile', headers={'Authorization': 'Bearer ' + self.token(account)})
        self.assertEqual(response.json['profile'], {'summary': 'alice'})
        self.assertEqual(self.client.get('/api/v1/jobs', headers={'Authorization': 'Bearer ' + self.token(account)}).status_code, 401)

    def test_mcp_discovery_initialize_and_unknown_tools(self):
        headers = {'Authorization': 'Bearer ' + self.token(), 'Accept': 'application/json, text/event-stream'}
        response = self.client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}}, headers=headers)
        self.assertEqual(response.json['result']['protocolVersion'], '2025-11-25')
        response = self.client.post('/mcp', json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, headers=headers)
        self.assertEqual([tool['name'] for tool in response.json['result']['tools']], ['get_profile'])
        response = self.client.post('/mcp', json={'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'run_shell', 'arguments': {}}}, headers=headers)
        self.assertEqual(response.json['error']['code'], -32602)
        self.assertEqual(self.client.get('/mcp', headers=headers).status_code, 405)

    def test_metadata_and_oauth_registration(self):
        response = self.client.get('/.well-known/oauth-protected-resource/mcp')
        self.assertEqual(response.json['resource'], 'https://example.com/mcp')
        response = self.client.post('/oauth/register', json={'redirect_uris': ['https://client.example/cb']})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['token_endpoint_auth_method'], 'none')
        self.assertEqual(self.client.post('/oauth/token', data={'grant_type': 'password'}).status_code, 400)

    def test_expired_token_and_account_version_revocation(self):
        expired = self.app.extensions['jobpilot_tokens'].issue('owner', 'v1', ['profile:read'], 'https://example.com/mcp', ttl=-1)
        self.assertEqual(self.client.get('/api/v1/profile', headers={'Authorization': 'Bearer ' + expired}).status_code, 401)
        account = self.app.extensions['jobpilot_accounts'].identity('email', 'a@example.com', verified=True)
        token = self.token(account)
        with self.app.extensions['jobpilot_accounts'].connect() as db:
            db.execute('UPDATE accounts SET version=? WHERE id=?', ('revoked', account['id']))
        self.assertEqual(self.client.get('/api/v1/profile', headers={'Authorization': 'Bearer ' + token}).status_code, 401)

    def test_authorization_requires_consent_nonce_and_bound_resource(self):
        from jinja2 import DictLoader
        from jobpilot.api_tokens import pkce_challenge
        self.app.jinja_loader = DictLoader({'mcp_authorize.html': '{{ consent_nonce }}'})
        client = self.app.extensions['jobpilot_tokens'].register_client({'redirect_uris': ['https://client.example/cb']})
        values = dict(client_id=client['client_id'], redirect_uri=client['redirect_uris'][0], response_type='code', scope='profile:read', resource='https://example.com/mcp', code_challenge_method='S256', code_challenge=pkce_challenge('v' * 43), state='opaque-state')
        with self.client.session_transaction() as browser:
            browser['auth_version'] = 'v1'
        page = self.client.get('/oauth/authorize', query_string=values)
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('code=', page.get_data(as_text=True))
        consent = self.client.post('/oauth/authorize', data={'consent_nonce': page.get_data(as_text=True), 'decision': 'allow'})
        self.assertTrue(consent.location.endswith('/oauth/complete'))
        consent = self.client.get(consent.location)
        from urllib.parse import urlsplit, parse_qs
        result = parse_qs(urlsplit(consent.location).query)
        self.assertEqual(result['state'], ['opaque-state'])
        exchange = self.client.post('/oauth/token', data=dict(grant_type='authorization_code', code=result['code'][0], client_id=client['client_id'], redirect_uri=client['redirect_uris'][0], code_verifier='v' * 43, resource=values['resource']))
        self.assertEqual(exchange.status_code, 200)
        self.assertIn('no-store', exchange.headers['Cache-Control'])
        replay = self.client.post('/oauth/authorize', data={'consent_nonce': page.get_data(as_text=True), 'decision': 'allow'})
        self.assertEqual(replay.status_code, 400)
        values['resource'] = 'https://attacker.example/mcp'
        self.assertEqual(self.client.get('/oauth/authorize', query_string=values).status_code, 400)

    def test_write_rejects_without_explicit_confirmation(self):
        token = self.token(allowed=['applications:write'])
        response = self.client.post('/api/v1/applications/prepare', json={'job_id': 'anything'}, headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.home / 'documents').exists())

    def test_prepare_current_snapshot_and_stale_approval(self):
        from jobpilot.applications import fingerprint, job_facts
        from jobpilot.core import Store, write_json
        profile = {'facts_verified': True, 'personal': {'first_name': 'Alice', 'last_name': 'Example', 'email': 'alice@example.com', 'phone': '123456789', 'city': 'Warsaw', 'country': 'Poland'}, 'skills': ['Python'], 'projects': [{'name': 'Sample project', 'bullets': ['Built a sample application.']}]}
        write_json(self.home / 'profile.json', profile)
        store = Store(self.home)
        store.add({'url': 'https://employer.example/job', 'title': 'Engineer', 'company': 'Example', 'description': 'Python engineer'})
        job = store.rows()[0]
        payload = {'confirmed': True, 'profile_version': fingerprint(profile), 'job_version': fingerprint(job_facts(job)), 'job_id': job['id'], 'request_key': 'prepare-1', 'cover_letter': False, 'optimization': 'off'}
        headers = {'Authorization': 'Bearer ' + self.token(allowed=['applications:write'])}
        first = self.client.post('/api/v1/applications/prepare', json=payload, headers=headers)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        repeated = self.client.post('/api/v1/applications/prepare', json=payload, headers=headers)
        self.assertEqual(repeated.json['application']['id'], first.json['application']['id'])
        run = first.json['application']
        approval = {'confirmed': True, 'application_id': run['id'], 'document_hash': run['document_hash'], 'profile_version': run['profile_hash'], 'job_version': run['job_hash']}
        profile['summary'] = 'Changed facts after preparation'
        write_json(self.home / 'profile.json', profile)
        rejected = self.client.post('/api/v1/applications/approve_documents', json=approval, headers=headers)
        self.assertEqual(rejected.status_code, 409)

    def test_api_approvals_share_credit_reservation_and_worker_queue(self):
        from jobpilot.applications import Applications, fingerprint, job_facts
        from jobpilot.billing import CreditLedger
        from jobpilot.core import Store, write_json
        from jobpilot.worker import TaskQueue
        account = self.app.extensions['jobpilot_accounts'].identity('email', 'credit-user@example.com', verified=True)
        home = self.home / 'accounts' / account['id']
        profile = {'facts_verified': True, 'personal': {'first_name': 'Alice', 'last_name': 'Example', 'email': 'alice@example.com', 'phone': '123456789', 'city': 'Warsaw', 'country': 'Poland'}, 'skills': ['Python'], 'projects': [{'name': 'Sample project', 'bullets': ['Built a sample application.']}]}
        write_json(home / 'profile.json', profile)
        store = Store(home)
        job_id = store.add({'url': 'https://employer.example/job', 'title': 'Engineer', 'company': 'Example', 'description': 'Python engineer'})
        apps = Applications(store)
        run = apps.prepare(job_id, profile, 'shared-queue', cover_letter=False, optimization='off')
        ledger = CreditLedger(self.home)
        self.app.extensions['jobpilot_credit_ledger'] = ledger
        ledger.grant(account['id'], 'test-credit-grant', 3, 'wallet')
        headers = {'Authorization': 'Bearer ' + self.token(account, ['applications:write'])}
        approval = {'confirmed': True, 'application_id': run['id'], 'document_hash': run['document_hash'], 'profile_version': run['profile_hash'], 'job_version': run['job_hash']}
        for _ in range(2):
            response = self.client.post('/api/v1/applications/approve_documents', json=approval, headers=headers)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(ledger.balance(account['id']), {'available': 2, 'reserved': 1})
        queue = TaskQueue(store)
        self.assertEqual([row['action'] for row in queue.rows()], ['fill'])
        apps.record_form(run['id'], {'fields': {'name': {'value': 'Alice Example'}}, 'unanswered_required': [], 'document_hash': run['document_hash']})
        approval['form_hash'] = apps.get(run['id'])['form_hash']
        for _ in range(2):
            response = self.client.post('/api/v1/applications/approve_form', json=approval, headers=headers)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(sorted(row['action'] for row in queue.rows()), ['fill', 'submit'])
        self.assertTrue(all(row['state'] == 'QUEUED' for row in queue.rows()))
        self.assertEqual(ledger.balance(account['id']), {'available': 2, 'reserved': 1})


if __name__ == '__main__':
    unittest.main()
