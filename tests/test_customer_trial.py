import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jobpilot.accounts import Accounts
from jobpilot.billing import CreditLedger
from web_support import WebCase


class TrialLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.accounts = Accounts(self.home)
        self.account = self.accounts.identity('email', 'trial@example.test', 'trial@example.test', verified=True)
        self.identifier = self.account['id']
        self.ledger = CreditLedger(self.home)

    def test_new_account_receives_ten_once_and_is_not_a_paying_customer(self):
        self.assertEqual(self.ledger.balance(self.identifier)['available'], 10)
        self.assertTrue(self.ledger.has_workspace_access(self.identifier))
        self.assertFalse(self.ledger.has_paid_access(self.identifier))
        self.accounts.identity('email', 'trial@example.test', 'trial@example.test', verified=True)
        self.assertEqual(CreditLedger(self.home).balance(self.identifier)['available'], 10)

    def test_tenth_success_exhausts_trial_and_eleventh_requires_payment(self):
        for number in range(10):
            reference = f'application:{number}'
            self.ledger.reserve(self.identifier, reference, '2026-10-25', 100)
            self.ledger.assert_paid_reservation(self.identifier, reference)
            self.ledger.settle(self.identifier, reference, True)
        self.assertEqual(self.ledger.balance(self.identifier)['available'], 0)
        self.assertFalse(self.ledger.has_workspace_access(self.identifier))
        with self.assertRaises(ValueError):
            self.ledger.reserve(self.identifier, 'eleventh', '2026-10-26', 100)

    def test_failure_and_refund_preserve_trial_and_do_not_create_paid_access(self):
        self.ledger.reserve(self.identifier, 'failed', '2026-10-25', 100)
        self.ledger.settle(self.identifier, 'failed', False)
        self.assertEqual(self.ledger.balance(self.identifier)['available'], 10)
        self.ledger.reserve(self.identifier, 'success', '2026-10-25', 100)
        self.ledger.settle(self.identifier, 'success', True)
        self.ledger.refund(self.identifier, 'success')
        self.ledger.refund(self.identifier, 'success')
        self.assertEqual(self.ledger.balance(self.identifier)['available'], 10)
        self.assertFalse(self.ledger.has_paid_access(self.identifier))

    def test_legacy_accounts_and_free_buckets_receive_no_trial(self):
        with self.accounts.connect() as db:
            db.execute('DELETE FROM trial_eligibility WHERE account_id=?', (self.identifier,))
        self.ledger.grant(self.identifier, 'legacy-free', 25, 'free')
        self.assertEqual(self.ledger.balance(self.identifier)['available'], 0)
        self.assertFalse(self.ledger.has_workspace_access(self.identifier))

    def test_concurrent_reservations_cannot_exceed_ten(self):
        def reserve(number):
            try:
                CreditLedger(self.home).reserve(self.identifier, str(number), '2026-10-25', 100)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(reserve, range(16)))
        self.assertEqual(sum(results), 10)
        self.assertEqual(self.ledger.balance(self.identifier), {'available': 0, 'reserved': 10})

    def test_trial_is_spent_before_purchased_credits(self):
        self.ledger.grant(self.identifier, 'paid-credit', 3, 'wallet')
        self.ledger.reserve(self.identifier, 'first-application', '2026-10-25', 100)
        self.ledger.settle(self.identifier, 'first-application', True)
        with self.ledger.connect() as db:
            self.assertEqual(db.execute("SELECT credits FROM credit_buckets WHERE kind='wallet'").fetchone()[0], 3)
        self.assertEqual(self.ledger.trial_status(self.identifier)['remaining'], 9)


class TrialAccessTests(WebCase):
    def test_signup_unlocks_workspace_and_scoped_api_with_ten_credits(self):
        response = self.post('/register', {'email':'newtrial@example.test',
            'password':'a secure customer password', 'confirm_password':'a secure customer password'}, page='/register')
        self.assertIn(b'Your job search', response.data)
        with self.client.session_transaction() as session:
            account = self.app.extensions['jobpilot_accounts'].get(session['account_id'])
        self.assertEqual(CreditLedger(self.home).balance(account['id'])['available'], 10)
        token = self.app.extensions['jobpilot_tokens'].issue(account['id'], account['version'], ['profile:read'], 'https://localhost/mcp')
        response = self.client.get('/api/v1/profile',base_url='https://localhost',headers={'Authorization':'Bearer '+token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/admin/customers', base_url='https://localhost').status_code, 403)
