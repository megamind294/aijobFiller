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
        with self.client.session_transaction() as sess:
            sess['account_id'] = account['id']
            sess['auth_version'] = account['version']
        self.assertEqual(self.client.get('/inbox?message=private', base_url='https://localhost').status_code, 404)
        page = self.client.get('/inbox', base_url='https://localhost').get_data(as_text=True)
        self.assertNotIn('Owner private message', page)


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
