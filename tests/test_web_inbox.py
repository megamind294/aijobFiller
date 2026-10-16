import base64
import json
import unittest
from unittest.mock import patch
from web_support import WebCase


class WebInboxCase(WebCase):
    def setUp(self):
        super().setUp()
        from jobpilot.web_inbox import register_inbox
        if 'inbox' not in self.app.view_functions:
            register_inbox(self.app)

    def test_requires_authentication_and_shows_unconfigured_state(self):
        self.assertEqual(self.client.get('/inbox', base_url='https://localhost').status_code, 302)
        self.login()
        response = self.client.get('/inbox', base_url='https://localhost')
        self.assertEqual(response.status_code, 200)
        self.assertIn('not configured', response.get_data(as_text=True))

    def test_hostile_message_is_escaped_and_thread_can_be_read(self):
        from jobpilot.inbox import Inbox
        from jobpilot.core import Store
        box = Inbox(Store(self.home))
        box.ingest([dict(id='a', thread_id='t', subject='<script>bad()</script>', text='<img src="https://evil.test/track">', timestamp=1),
                    dict(id='b', thread_id='t', subject='Second reply', text='Thread reply', timestamp=2),
                    dict(id='ambiguous', subject='Unlinked interview', text='interview invitation', timestamp=3)])
        self.login()
        page = self.client.get('/inbox?message=a', base_url='https://localhost').get_data(as_text=True)
        self.assertNotIn('<script>bad()', page)
        self.assertNotIn('<img src="https://evil.test', page)
        self.assertIn('&lt;img', page)
        self.assertIn('Thread reply', page)
        review = self.client.get('/inbox?category=REVIEW', base_url='https://localhost').get_data(as_text=True)
        self.assertIn('Unlinked interview', review)

    def configure(self):
        self.app.config.update(GOOGLE_CLIENT_ID='client', GOOGLE_CLIENT_SECRET='secret',
                               GOOGLE_GMAIL_REDIRECT_URI='https://localhost/inbox/callback',
                               MAILBOX_ENCRYPTION_KEY=base64.urlsafe_b64encode(b'a' * 32).decode())

    def test_oauth_state_one_time_even_after_exchange_failure(self):
        self.configure()
        self.login()
        self.client.post('/inbox/connect', data={'csrf_token': self.csrf(path='/inbox')}, base_url='https://localhost')
        with self.client.session_transaction() as sess:
            state = sess['inbox_oauth_state']
            verifier = sess['inbox_oauth_verifier']
        with patch('jobpilot.mailbox_provider.GmailMailbox.exchange', side_effect=ValueError('private provider response')):
            first = self.client.get('/inbox/callback?state=' + state + '&code=test', base_url='https://localhost')
            # Simulate replaying an older, still-valid signed browser cookie.
            with self.client.session_transaction() as sess:
                sess['inbox_oauth_state'] = state
                sess['inbox_oauth_verifier'] = verifier
            second = self.client.get('/inbox/callback?state=' + state + '&code=test', base_url='https://localhost')
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 400)
        self.assertNotIn('private provider response', first.get_data(as_text=True))

    def test_missing_state_rejected_and_tokens_encrypted(self):
        from jobpilot.mailbox_provider import GmailMailbox
        self.configure()
        self.login()
        self.assertEqual(self.client.get('/inbox/callback?code=test', base_url='https://localhost').status_code, 400)
        provider = GmailMailbox(self.home, self.app.config)
        provider.save_tokens({'access_token': 'sensitive', 'refresh_token': 'refresh'})
        self.assertNotIn(b'sensitive', (self.home / 'mailbox' / 'tokens.enc').read_bytes())
        self.assertEqual(provider.load_tokens()['refresh_token'], 'refresh')
        self.post('/inbox/disconnect', page='/inbox')
        self.assertFalse(provider.connected())

    def test_get_connect_does_not_start_oauth(self):
        self.configure()
        self.login()
        response = self.client.get('/inbox/connect', base_url='https://localhost')
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertNotIn('inbox_oauth_state', sess)

    def test_all_mutating_routes_require_session_and_csrf(self):
        for path in ['/inbox/sync', '/inbox/connect', '/inbox/disconnect', '/inbox/a/route', '/inbox/a/draft']:
            self.assertEqual(self.client.post(path, base_url='https://localhost').status_code, 302)
        self.login()
        self.assertEqual(self.client.post('/inbox/disconnect', base_url='https://localhost').status_code, 400)

    def test_customer_cannot_read_or_route_owner_message(self):
        from jobpilot.inbox import Inbox
        from jobpilot.core import Store
        box = Inbox(Store(self.home))
        box.ingest([dict(id='private', text='Owner private message', timestamp=1)])
        accounts = self.app.extensions['jobpilot_accounts']
        account = accounts.identity('email', 'other@example.test', 'other@example.test', verified=True)
        from jobpilot.billing import CreditLedger
        CreditLedger(self.home).grant(account['id'], 'paid-isolation-fixture', 1, 'wallet')
        with self.client.session_transaction() as sess:
            sess['account_id'] = account['id']
            sess['auth_version'] = account['version']
        self.assertEqual(self.client.get('/inbox?message=private', base_url='https://localhost').status_code, 404)
        page = self.client.get('/inbox', base_url='https://localhost').get_data(as_text=True)
        self.assertNotIn('Owner private message', page)


    def draft_fixture(self):
        from jobpilot.inbox import Inbox
        from jobpilot.core import Store
        box = Inbox(Store(self.home))
        box.ingest([dict(id='selected', subject='Selected message', text='a' * 13000, timestamp=1),
                    dict(id='other', text='other-message-private', timestamp=2)])
        box.save_draft('selected', 'Prior saved draft')
        (self.home / 'profile.json').write_text(json.dumps({'personal': {'first_name': 'Ada',
            'email': 'private@example.test', 'phone': 'private-phone'}, 'summary': 'Confirmed summary',
            'experience': [{'secret': 'private-experience'}]}), encoding='utf-8')
        self.login()
        return box

    def test_gemini_draft_is_explicit_minimal_local_and_escaped(self):
        box = self.draft_fixture()
        self.app.config['GEMINI_API_KEY'] = 'mock-configured'
        page = self.client.get('/inbox?message=selected', base_url='https://localhost').get_data(as_text=True)
        self.assertIn('By choosing Draft with Gemini', page)
        self.assertIn('Google Gemini', page)
        with patch('jobpilot.web_inbox.provider_reply', return_value='<script>draft()</script>') as provider, \
             patch('jobpilot.web_inbox.claim_ai_request') as budget:
            response = self.post('/inbox/selected/draft', {'action': 'gemini'}, page='/inbox?message=selected')
        self.assertEqual(provider.call_count, 1)
        budget.assert_called_once_with()
        args, kwargs = provider.call_args
        self.assertEqual(args[0], [{'role': 'user', 'content': 'a' * 12000}])
        self.assertEqual(args[1], {'candidate': {'first_name': 'Ada', 'summary': 'Confirmed summary'}})
        self.assertEqual(kwargs['max_output_tokens'], 700)
        self.assertIn('untrusted data', kwargs['instructions'])
        self.assertEqual(box.get('selected')['draft'], '<script>draft()</script>')
        self.assertEqual(box.get('other')['draft'], '')
        self.assertNotIn('<script>draft()', response.get_data(as_text=True))
        self.assertIn('&lt;script&gt;draft()', response.get_data(as_text=True))
        self.assertIn('Local draft saved. Nothing was sent', response.get_data(as_text=True))

    def test_manual_and_acknowledgement_drafts_never_call_gemini(self):
        box = self.draft_fixture()
        self.app.config['GEMINI_API_KEY'] = 'mock-configured'
        with patch('jobpilot.web_inbox.provider_reply') as provider:
            self.post('/inbox/selected/draft', {'draft': 'Manual reply'}, page='/inbox')
            self.assertEqual(box.get('selected')['draft'], 'Manual reply')
            self.post('/inbox/selected/draft', {'action': 'suggest'}, page='/inbox')
        provider.assert_not_called()
        self.assertIn('Thank you', box.get('selected')['draft'])

    def test_unconfigured_gemini_preserves_draft_and_does_not_claim_success(self):
        box = self.draft_fixture()
        self.app.config['GEMINI_API_KEY'] = ''
        with patch('jobpilot.web_inbox.provider_reply') as provider, \
             patch('jobpilot.web_inbox.claim_ai_request') as budget:
            response = self.post('/inbox/selected/draft', {'action': 'gemini'}, page='/inbox')
        provider.assert_not_called()
        budget.assert_not_called()
        self.assertEqual(box.get('selected')['draft'], 'Prior saved draft')
        self.assertIn('not configured', response.get_data(as_text=True))
        self.assertNotIn('Local draft saved', response.get_data(as_text=True))

    def test_gemini_failure_and_shared_rate_limit_preserve_saved_draft(self):
        from jobpilot.assistant import AssistantUnavailable, AILimitReached
        box = self.draft_fixture()
        self.app.config['GEMINI_API_KEY'] = 'mock-configured'
        with patch('jobpilot.web_inbox.provider_reply', side_effect=AssistantUnavailable('Try again later.')), \
             patch('jobpilot.web_inbox.claim_ai_request'):
            response = self.post('/inbox/selected/draft', {'action': 'gemini'}, page='/inbox')
        self.assertIn('saved draft is unchanged', response.get_data(as_text=True))
        self.assertEqual(box.get('selected')['draft'], 'Prior saved draft')
        with patch('jobpilot.web_inbox.claim_ai_request', side_effect=AILimitReached('Hourly AI limit reached.')), \
             patch('jobpilot.web_inbox.provider_reply') as provider:
            response = self.post('/inbox/selected/draft', {'action': 'gemini'}, page='/inbox')
        provider.assert_not_called()
        self.assertIn('Hourly AI limit reached', response.get_data(as_text=True))
        self.assertEqual(box.get('selected')['draft'], 'Prior saved draft')

    def test_ingestion_failure_does_not_advance_page_cursor(self):
        import sqlite3
        from urllib.parse import parse_qs, urlsplit
        from jobpilot.mailbox_provider import GmailMailbox
        from jobpilot.inbox import Inbox
        from jobpilot.core import Store
        self.configure()
        provider = GmailMailbox(self.home, self.app.config)
        provider.save_tokens(dict(access_token='mock-access', refresh_token='mock-refresh',
                                  expires_at=9999999999, mailbox='candidate@example.test'))
        self.login()
        def source(url, data=None, token=None):
            query = parse_qs(urlsplit(url).query)
            if 'q' in query:
                offset = int(query.get('pageToken', ['0'])[0])
                result = {'messages': [{'id': str(i)} for i in range(offset, min(offset+50, 60))]}
                if offset == 0:
                    result['nextPageToken'] = '50'
                return result
            return {'id': urlsplit(url).path.rsplit('/', 1)[-1], 'payload': {}}
        with patch('jobpilot.mailbox_provider.GmailMailbox._http', side_effect=source) as http:
            with patch('jobpilot.inbox.Inbox.ingest', side_effect=sqlite3.OperationalError('busy')):
                response = self.post('/inbox/sync', page='/inbox')
            self.assertIn('Could not sync Gmail', response.get_data(as_text=True))
            self.assertNotIn('sync_cursor', provider.load_tokens())
            self.assertEqual(provider.load_tokens()['sync_pending']['next_page'], '50')
            self.assertEqual(http.call_count, 51)
            http.reset_mock()
            self.post('/inbox/sync', page='/inbox')
            self.assertEqual(provider.load_tokens()['sync_cursor']['page_token'], '50')
            self.assertNotIn('sync_pending', provider.load_tokens())
            self.assertEqual(http.call_count, 50)
            self.post('/inbox/sync', page='/inbox')
        self.assertEqual(len(Inbox(Store(self.home)).rows()), 60)
        self.assertNotIn('sync_cursor', provider.load_tokens())


class ProviderCase(unittest.TestCase):
    def test_read_only_oauth_scope_exact_redirect_and_pkce(self):
        from jobpilot.mailbox_provider import GmailMailbox
        from urllib.parse import parse_qs, urlsplit
        import tempfile
        config = dict(GOOGLE_CLIENT_ID='client', GOOGLE_CLIENT_SECRET='secret',
                      GOOGLE_GMAIL_REDIRECT_URI='https://example.test/inbox/callback',
                      MAILBOX_ENCRYPTION_KEY=base64.urlsafe_b64encode(b'a' * 32).decode())
        with tempfile.TemporaryDirectory() as home:
            provider = GmailMailbox(home, config)
            query = parse_qs(urlsplit(provider.authorization_url('one-time', 'x' * 64)).query)
            self.assertEqual(query['scope'], ['https://www.googleapis.com/auth/gmail.readonly'])
            self.assertEqual(query['redirect_uri'], ['https://example.test/inbox/callback'])
            self.assertEqual(query['code_challenge_method'], ['S256'])
            config['GOOGLE_GMAIL_REDIRECT_URI'] = 'http://example.test/inbox/callback'
            self.assertFalse(provider.configured())

    def test_bounded_fetch_and_mailbox_namespaced_dedup_identifiers(self):
        from jobpilot.mailbox_provider import GmailMailbox
        import tempfile
        config = dict(GOOGLE_CLIENT_ID='client', GOOGLE_CLIENT_SECRET='secret',
                      GOOGLE_GMAIL_REDIRECT_URI='https://example.test/inbox/callback',
                      MAILBOX_ENCRYPTION_KEY=base64.urlsafe_b64encode(b'a' * 32).decode())
        with tempfile.TemporaryDirectory() as home:
            provider = GmailMailbox(home, config)
            provider.save_tokens(dict(access_token='token', refresh_token='refresh', expires_at=9999999999, mailbox='a@example.test'))
            def response(url, data=None, token=None):
                if '?q=' in url:
                    self.assertIn('maxResults=50', url)
                    return {'messages': [{'id': str(i)} for i in range(80)]}
                return {'id': url.split('/')[-1].split('?')[0], 'threadId': 'thread', 'payload': {}}
            with patch.object(provider, '_http', side_effect=response):
                messages = provider.candidates(1000)
            self.assertEqual(len(messages), 50)
            self.assertNotEqual(messages[0]['id'], '0')
            self.assertTrue(messages[0]['id'].endswith(':0'))


if __name__ == '__main__':
    unittest.main()
