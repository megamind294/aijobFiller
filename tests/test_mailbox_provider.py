import base64
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from jobpilot.mailbox_provider import GmailMailbox, MailboxUnavailable, QUERY


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = dict(GOOGLE_CLIENT_ID='client', GOOGLE_CLIENT_SECRET='secret',
            GOOGLE_GMAIL_REDIRECT_URI='https://example.test/inbox/callback',
            MAILBOX_ENCRYPTION_KEY=base64.urlsafe_b64encode(b'a' * 32).decode())
        self.provider = GmailMailbox(self.tmp.name, self.config)
        self.provider.save_tokens(dict(access_token='token', refresh_token='refresh',
            expires_at=9999999999, mailbox='person@example.test'))

    def response(self, url, data=None, token=None):
        query = parse_qs(urlsplit(url).query)
        if 'q' in query:
            offset = int(query.get('pageToken', ['0'])[0])
            size = int(query['maxResults'][0])
            end = min(120, offset + size)
            result = {'messages': [{'id': str(i)} for i in range(offset, end)]}
            if end < 120:
                result['nextPageToken'] = str(end)
            return result
        return {'id': urlsplit(url).path.rsplit('/', 1)[-1], 'payload': {}}

    def test_over_fifty_messages_continue_across_provider_instances_then_restart(self):
        batches = []
        for _ in range(4):
            provider = GmailMailbox(self.tmp.name, self.config)
            with patch.object(provider, '_http', side_effect=self.response) as requests:
                batches.append(provider.candidates())
                self.assertLessEqual(requests.call_count, 51)
                provider.acknowledge_candidates()
        self.assertEqual([len(batch) for batch in batches], [50, 50, 20, 50])
        self.assertEqual(len({msg['id'] for batch in batches[:3] for msg in batch}), 120)
        self.assertEqual(batches[0], batches[3])
        self.assertNotIn(b'page_token', self.provider.path.read_bytes())

    def test_source_failure_retains_prior_page_for_retry(self):
        with patch.object(self.provider, '_http', side_effect=self.response):
            self.provider.candidates()
            self.provider.acknowledge_candidates()
        before = self.provider.load_tokens()
        def failure(url, **kwargs):
            if '/51?' in url:
                raise MailboxUnavailable('temporary source failure')
            return self.response(url, **kwargs)
        with patch.object(self.provider, '_http', side_effect=failure):
            with self.assertRaises(MailboxUnavailable):
                self.provider.candidates()
        after = self.provider.load_tokens()
        self.assertEqual(after['sync_cursor'], before['sync_cursor'])
        self.assertEqual(after['sync_pending']['message_ids'][0], '50')
        with patch.object(self.provider, '_http', side_effect=self.response):
            batch = self.provider.candidates()
        self.assertTrue(batch[0]['id'].endswith(':50'))

    def test_invalid_page_token_resets_for_next_sync_without_extra_requests(self):
        with patch.object(self.provider, '_http', side_effect=self.response):
            self.provider.candidates()
            self.provider.acknowledge_candidates()
        error = HTTPError('https://gmail.googleapis.com/', 400, 'Bad request', {}, None)
        with patch('jobpilot.mailbox_provider.urlopen', side_effect=error) as source:
            with self.assertRaises(MailboxUnavailable):
                self.provider.candidates()
        self.assertEqual(source.call_count, 1)
        with patch.object(self.provider, '_http', side_effect=self.response):
            batch = self.provider.candidates()
        self.assertTrue(batch[0]['id'].endswith(':0'))

    def test_transient_list_failure_does_not_reset_cursor(self):
        with patch.object(self.provider, '_http', side_effect=self.response):
            self.provider.candidates()
            self.provider.acknowledge_candidates()
        before = self.provider.load_tokens()
        error = HTTPError('https://gmail.googleapis.com/', 503, 'Unavailable', {}, None)
        with patch('jobpilot.mailbox_provider.urlopen', side_effect=error):
            with self.assertRaises(MailboxUnavailable):
                self.provider.candidates()
        self.assertEqual(self.provider.load_tokens(), before)

    def test_changed_mailbox_or_query_starts_from_first_page(self):
        for changed in ({'mailbox': 'other@example.test'}, {'query': 'old query'}):
            tokens = self.provider.load_tokens()
            tokens['sync_cursor'] = dict(mailbox='person@example.test', query=QUERY, page_token='50')
            if 'mailbox' in changed:
                tokens['mailbox'] = changed['mailbox']
            else:
                tokens['sync_cursor'].update(changed)
            self.provider.save_tokens(tokens)
            with patch.object(self.provider, '_http', side_effect=self.response):
                batch = self.provider.candidates()
            self.assertTrue(batch[0]['id'].endswith(':0'))

    def test_separate_account_storage_does_not_share_cursor(self):
        with patch.object(self.provider, '_http', side_effect=self.response):
            self.provider.candidates()
            self.provider.acknowledge_candidates()
        with tempfile.TemporaryDirectory() as other_home:
            other = GmailMailbox(other_home, self.config)
            tokens = self.provider.load_tokens()
            tokens.pop('sync_cursor', None)
            other.save_tokens(tokens)
            with patch.object(other, '_http', side_effect=self.response):
                batch = other.candidates()
            self.assertTrue(batch[0]['id'].endswith(':0'))

    def test_cursor_write_preserves_concurrent_access_token_refresh(self):
        def refresh(url, **kwargs):
            tokens = self.provider.load_tokens()
            tokens['access_token'] = 'newly-refreshed-token'
            self.provider.save_tokens(tokens)
            return self.response(url, **kwargs)
        with patch.object(self.provider, '_http', side_effect=refresh):
            self.provider.candidates(1)
        self.assertEqual(self.provider.load_tokens()['access_token'], 'newly-refreshed-token')

    def test_unacknowledged_page_retries_after_provider_restart(self):
        with patch.object(self.provider, '_http', side_effect=self.response):
            batch = self.provider.candidates()
        tokens = self.provider.load_tokens()
        self.assertNotIn('sync_cursor', tokens)
        self.assertEqual(tokens['sync_pending']['next_page'], '50')
        restarted = GmailMailbox(self.tmp.name, self.config)
        with patch.object(restarted, '_http', side_effect=self.response):
            self.assertEqual(restarted.candidates(), batch)
        restarted.acknowledge_candidates()
        self.assertEqual(restarted.load_tokens()['sync_cursor']['page_token'], '50')
        self.assertNotIn('sync_pending', restarted.load_tokens())

    def test_other_request_cannot_acknowledge_uningested_pending_page(self):
        with patch.object(self.provider, '_http', side_effect=self.response):
            self.provider.candidates()
        other = GmailMailbox(self.tmp.name, self.config)
        with patch.object(other, '_http', side_effect=self.response):
            other.candidates()
        with self.assertRaises(MailboxUnavailable):
            self.provider.acknowledge_candidates()
        self.assertNotIn('sync_cursor', other.load_tokens())
        other.acknowledge_candidates()

    def test_pending_ids_survive_new_mail_and_smaller_retry_limit(self):
        with patch.object(self.provider, '_http', side_effect=self.response):
            batch = self.provider.candidates()
        def retry_source(url, **kwargs):
            self.assertNotIn('q=', url, 'Pending page must not be re-listed')
            return self.response(url, **kwargs)
        with patch.object(self.provider, '_http', side_effect=retry_source) as http:
            first = self.provider.candidates(10)
            self.assertEqual(first, batch[:10])
            self.assertEqual(http.call_count, 10)
            self.provider.acknowledge_candidates()
            self.assertNotIn('sync_cursor', self.provider.load_tokens())
            self.assertEqual(self.provider.load_tokens()['sync_pending']['message_ids'][0], '10')
            rest = self.provider.candidates()
            self.assertEqual(rest, batch[10:])
            self.provider.acknowledge_candidates()
        self.assertEqual(self.provider.load_tokens()['sync_cursor']['page_token'], '50')

    def test_deleted_pending_message_does_not_starve_rest_of_page(self):
        from jobpilot.mailbox_provider import MailboxMessageGone
        with patch.object(self.provider, '_http', side_effect=self.response):
            self.provider.candidates(2)
        def source(url, **kwargs):
            if '/0?' in url:
                raise MailboxMessageGone('Deleted')
            return self.response(url, **kwargs)
        with patch.object(self.provider, '_http', side_effect=source):
            messages = self.provider.candidates()
            self.provider.acknowledge_candidates()
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]['id'].endswith(':1'))
        self.assertEqual(self.provider.load_tokens()['sync_cursor']['page_token'], '2')

    def test_concurrent_disconnect_is_not_undone_by_cursor_write(self):
        def disconnect(url, **kwargs):
            self.provider.disconnect()
            return self.response(url, **kwargs)
        with patch.object(self.provider, '_http', side_effect=disconnect):
            with self.assertRaises(MailboxUnavailable):
                self.provider.candidates(1)
        self.assertFalse(self.provider.connected())

    def test_concurrent_reconnection_is_not_overwritten_by_old_scan(self):
        def reconnect(url, **kwargs):
            tokens = self.provider.load_tokens()
            tokens['mailbox'] = 'new@example.test'
            tokens['refresh_token'] = 'new-connection'
            self.provider.save_tokens(tokens)
            return self.response(url, **kwargs)
        with patch.object(self.provider, '_http', side_effect=reconnect):
            with self.assertRaises(MailboxUnavailable):
                self.provider.candidates(1)
        self.assertEqual(self.provider.load_tokens()['mailbox'], 'new@example.test')
        self.assertNotIn('sync_cursor', self.provider.load_tokens())


if __name__ == '__main__':
    unittest.main()
