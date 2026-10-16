import time
from web_support import WebCase
from jobpilot.billing import CreditLedger


class PaidAccessTests(WebCase):
    def setUp(self):
        super().setUp()
        self.account = self.app.extensions['jobpilot_accounts'].identity(
            'email', 'customer@example.test', email='customer@example.test', verified=True)
        self.ledger = CreditLedger(self.home)
        with self.client.session_transaction() as session:
            session['account_id'] = self.account['id']
            session['auth_version'] = self.account['version']

    def test_unpaid_customer_must_pay_even_with_old_free_credits(self):
        self.ledger.grant(self.account['id'], 'legacy-free', 25, 'free')
        for path in ('/', '/browse', '/profile', '/inbox', '/applications', '/auto-apply'):
            response = self.client.get(path, base_url='https://localhost')
            self.assertEqual(response.status_code, 302, path)
            self.assertEqual(response.location, '/billing')
        response = self.client.get('/billing?success=1', base_url='https://localhost')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Payment required', response.data)
        self.assertFalse(self.ledger.has_paid_access(self.account['id']))

    def test_owner_needs_no_payment_or_provider_configuration(self):
        with self.client.session_transaction() as session:
            session.clear()
        self.login()
        self.assertEqual(self.client.get('/browse', base_url='https://localhost').status_code, 200)
        self.assertEqual(self.ledger.balance('owner')['available'], 0)

    def test_confirmed_paid_credit_unlocks_customer_and_expiry_locks_again(self):
        self.ledger.grant(self.account['id'], 'paid-fixture', 2, 'wallet', time.time() + 3600)
        self.assertEqual(self.client.get('/browse', base_url='https://localhost').status_code, 200)
        with self.ledger.connect() as db:
            db.execute('UPDATE credit_buckets SET expires=?', (time.time() - 1,))
        self.assertEqual(self.client.get('/browse', base_url='https://localhost').location, '/billing')

    def test_bearer_token_cannot_bypass_payment(self):
        bearer = self.app.extensions['jobpilot_tokens'].issue(self.account['id'], self.account['version'],
                    ['profile:read'], 'https://localhost/mcp')
        response = self.client.get('/api/v1/profile', base_url='https://localhost',
                                   headers={'Authorization':'Bearer ' + bearer})
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json['error'], 'payment_required')
        response = self.client.post('/mcp', base_url='https://localhost',
                    headers={'Authorization':'Bearer ' + bearer},
                    json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
        self.assertEqual(response.status_code, 402)

    def test_old_free_credits_are_neither_displayed_nor_spent(self):
        self.ledger.grant(self.account['id'], 'legacy-free', 25, 'free')
        self.assertEqual(self.ledger.balance(self.account['id'])['available'], 0)
        with self.assertRaises(ValueError):
            self.ledger.reserve(self.account['id'], 'no-free-apps', '2026-10-16', 10)

    def test_confirmed_paid_period_unlocks_even_after_credits_run_out(self):
        stamp = time.time()
        with self.ledger.connect() as db:
            db.execute('INSERT INTO subscriptions(account_id,tier,status,subscription_id,paid_start,paid_through) VALUES(?,?,?,?,?,?)',
                       (self.account['id'], 'starter', 'active', 'sub_paid_fixture', stamp - 60, stamp + 3600))
        self.ledger.refresh(self.account['id'])
        with self.ledger.connect() as db:
            db.execute('UPDATE credit_buckets SET credits=0')
        self.assertEqual(self.client.get('/browse', base_url='https://localhost').status_code, 200)

    def test_registration_and_billing_do_not_grant_free_trial(self):
        self.client.get('/billing', base_url='https://localhost')
        self.assertEqual(self.ledger.balance(self.account['id'])['available'], 0)

    def test_unpaid_worker_leaves_task_queued(self):
        from jobpilot.core import Store
        from jobpilot.worker import ApplicationWorker, TaskQueue
        store = Store(self.home / 'accounts' / self.account['id'])
        queue = TaskQueue(store)
        queue.enqueue('not-yet-submitted', 'fill')
        worker = ApplicationWorker(store, ledger=self.ledger, account_id=self.account['id'])
        self.assertFalse(worker.run_once())
        self.assertEqual(queue.rows()[0]['state'], 'QUEUED')

    def legacy_reservation(self, reference):
        self.ledger.grant(self.account['id'], 'legacy-free', 25, 'free')
        self.ledger.grant(self.account['id'], 'purchased-fixture', 2, 'wallet')
        with self.ledger.connect() as db:
            bucket = db.execute("SELECT id FROM credit_buckets WHERE grant_key='legacy-free'").fetchone()[0]
            db.execute('INSERT INTO credit_reservations VALUES(?,?,?,?,?,?,?)',
                       (reference, self.account['id'], bucket, '2026-10-16', 'reserved', time.time(), time.time()))

    def test_existing_free_reservation_is_rejected_after_purchase(self):
        self.legacy_reservation('legacy-application')
        with self.assertRaises(ValueError):
            self.ledger.reserve(self.account['id'], 'legacy-application', '2026-10-16', 10)

    def test_queued_free_reservation_cannot_execute_after_purchase(self):
        from jobpilot.core import Store, write_json
        from jobpilot.applications import Applications
        from jobpilot.worker import ApplicationWorker, TaskQueue
        from test_workflow import PROFILE
        store = Store(self.home / 'accounts' / self.account['id'])
        write_json(store.home / 'profile.json', PROFILE)
        job = store.add({'url':'https://example.com/job','company':'Example','title':'React','description':'React'})
        apps = Applications(store)
        run = apps.prepare(job, PROFILE, 'legacy-queued')
        apps.approve_documents(run['id'], run['document_hash'])
        self.legacy_reservation('application:' + run['id'])
        queue = TaskQueue(store)
        queue.enqueue(run['id'], 'fill')
        ApplicationWorker(store, ledger=self.ledger, account_id=self.account['id']).run_once()
        self.assertIn('paid credits', apps.get(run['id'])['error'])
        self.assertEqual(self.ledger.balance(self.account['id'])['available'], 2)
        self.assertFalse(queue.rows()[0]['attempted'])
