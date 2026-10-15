import tempfile
import unittest
from pathlib import Path


class AccountTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.accounts import Accounts
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.accounts = Accounts(Path(self.tmp.name))

    def test_verified_identity_is_required_and_never_grants_owner(self):
        with self.assertRaises(ValueError):
            self.accounts.identity('google', 'subject', 'user@example.com', verified=False)
        account = self.accounts.identity('google', 'subject', 'user@example.com', verified=True)
        self.assertEqual(account['role'], 'customer')
        self.assertEqual(self.accounts.identity('google', 'subject', 'user@example.com', verified=True)['id'], account['id'])

    def test_email_collision_does_not_silently_link_accounts(self):
        self.accounts.identity('google', 'first', 'user@example.com', verified=True)
        with self.assertRaises(ValueError):
            self.accounts.identity('email', 'user@example.com', 'user@example.com', verified=True)

    def test_verification_challenge_expires_and_can_be_consumed_once(self):
        token = self.accounts.issue_challenge('email', 'user@example.com', '123456', 'server-secret', stamp=100)
        with self.assertRaises(ValueError):
            self.accounts.consume_challenge(token, '111111', 'server-secret', stamp=101)
        identity = self.accounts.consume_challenge(token, '123456', 'server-secret', stamp=102)
        self.assertEqual(identity, ('email', 'user@example.com'))
        with self.assertRaises(ValueError):
            self.accounts.consume_challenge(token, '123456', 'server-secret', stamp=103)
        expired = self.accounts.issue_challenge('email', 'other@example.com', '123456', 'server-secret', stamp=100)
        with self.assertRaises(ValueError):
            self.accounts.consume_challenge(expired, '123456', 'server-secret', stamp=701)

    def test_revoke_changes_session_version(self):
        account = self.accounts.identity('email', 'user@example.com', 'user@example.com', verified=True)
        self.accounts.revoke(account['id'])
        self.assertNotEqual(account['version'], self.accounts.get(account['id'])['version'])
