import json
import time
from web_support import WebCase
from jobpilot.billing import CreditLedger


class CustomerAdminTests(WebCase):
    def setUp(self):
        super().setUp()
        self.accounts = self.app.extensions['jobpilot_accounts']
        self.customer = self.accounts.identity('email', 'unpaid@example.test', 'unpaid@example.test', verified=True)
        self.paid = self.accounts.identity('google', 'paid-subject', 'paid@example.test', verified=True)
        self.ledger = CreditLedger(self.home)
        self.ledger.balance(self.customer['id'])
        with self.ledger.connect() as db:
            db.execute('INSERT INTO billing_events VALUES(?,?,?,?,?)',
                ('evt_test_paid', self.paid['id'], 'invoice.paid', json.dumps({'provider_object':'invoice_test','livemode':False}), time.time()))
        self.login()

    def test_owner_sees_accounts_and_distinguishes_purchase_history_from_trial(self):
        response = self.client.get('/admin/customers', base_url='https://localhost')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'unpaid@example.test', response.data)
        self.assertIn(b'paid@example.test', response.data)
        self.assertIn(b'Sandbox payment', response.data)
        self.assertIn(b'No purchase', response.data)
        self.assertNotIn(b'password_hash', response.data)

    def test_purchase_filter_and_email_search(self):
        response = self.client.get('/admin/customers?purchase=yes', base_url='https://localhost')
        self.assertIn(b'paid@example.test', response.data)
        self.assertNotIn(b'unpaid@example.test', response.data)
        response = self.client.get('/admin/customers?q=unpaid', base_url='https://localhost')
        self.assertIn(b'unpaid@example.test', response.data)
        self.assertNotIn(b'>paid@example.test<', response.data)

    def test_legacy_payment_mode_is_not_assumed_live(self):
        with self.ledger.connect() as db:
            db.execute('UPDATE billing_events SET payload=?', (json.dumps({'provider_object':'old_invoice'}),))
        response = self.client.get('/admin/customers', base_url='https://localhost')
        self.assertIn(b'Mode not recorded', response.data)

    def test_customers_and_anonymous_users_cannot_view_customer_database(self):
        with self.client.session_transaction() as session:
            session.clear()
        self.assertEqual(self.client.get('/admin/customers', base_url='https://localhost').status_code, 302)
        with self.client.session_transaction() as session:
            session['account_id'] = self.customer['id']
            session['auth_version'] = self.customer['version']
        self.assertEqual(self.client.get('/admin/customers', base_url='https://localhost').status_code, 403)
