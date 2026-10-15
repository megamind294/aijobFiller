import tempfile
import unittest
from pathlib import Path
from jobpilot.core import Store, write_json
from jobpilot.applications import Applications
from test_workflow import PROFILE


class QueueTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.worker import TaskQueue
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.queue = TaskQueue(self.store)

    def test_restart_and_lease_reclaim_only_filling(self):
        self.queue.enqueue('app', 'fill')
        task = self.queue.claim('worker-a', stamp=100, lease_seconds=10)
        self.assertIsNone(self.queue.claim('worker-b', stamp=105))
        from jobpilot.worker import TaskQueue
        recovered = TaskQueue(self.store).claim('worker-b', stamp=111)
        self.assertEqual(task['id'], recovered['id'])
        with self.assertRaises(ValueError):
            self.queue.complete(task['id'], 'worker-a')

    def test_expired_submission_is_never_retried(self):
        self.queue.enqueue('app', 'submit')
        task = self.queue.claim('worker-a', stamp=100, lease_seconds=10)
        self.queue.mark_attempt(task['id'], 'worker-a')
        self.assertIsNone(self.queue.claim('worker-b', stamp=111))
        self.assertEqual(self.queue.rows()[0]['state'], 'UNCERTAIN')

    def test_enqueue_is_idempotent(self):
        first = self.queue.enqueue('app', 'fill')
        self.assertEqual(first, self.queue.enqueue('app', 'fill'))
        self.assertEqual(len(self.queue.rows()), 1)

    def test_worker_entrypoint_defaults_to_one_on_demand_task(self):
        from jobpilot.worker import main
        self.assertEqual(main(['--root', str(self.store.home), '--account', 'owner']), 0)

    def test_global_heavy_lock_leaves_queue_unclaimed(self):
        from jobpilot.worker import ApplicationWorker
        from jobpilot.service import ProcessLock
        from jobpilot.worker import TaskQueue
        self.queue.enqueue('app', 'fill')
        customer_store = Store(self.store.home / 'accounts' / ('a' * 32))
        customer_queue = TaskQueue(customer_store)
        customer_queue.enqueue('customer-app', 'fill')
        with ProcessLock(self.store.home / 'heavy-work.lock'):
            self.assertFalse(ApplicationWorker(self.store).run_once())
            self.assertFalse(ApplicationWorker(customer_store, lock_root=self.store.home).run_once())
        self.assertEqual(self.queue.rows()[0]['state'], 'QUEUED')
        self.assertEqual(customer_queue.rows()[0]['state'], 'QUEUED')

    def test_root_entrypoint_can_process_existing_workspaces_once(self):
        from jobpilot.worker import main
        self.assertEqual(main(['--home', str(self.store.home), '--once']), 0)

    def test_worker_reconciles_receipt_after_crash_before_credit_settlement(self):
        from jobpilot.worker import run_workspaces
        from jobpilot.billing import CreditLedger
        from datetime import date
        write_json(self.store.home / 'profile.json', PROFILE)
        key = self.store.add({'url': 'https://example.com/job', 'title': 'React', 'company': 'Example', 'description': 'React'})
        apps = Applications(self.store)
        run = apps.prepare(key, PROFILE, 'reconcile')
        apps.approve_documents(run['id'], run['document_hash'])
        apps.record_form(run['id'], {'fields': {'Name': 'Demo'}, 'unanswered_required': []})
        apps.approve_form(run['id'], apps.get(run['id'])['form_hash'])
        ledger = CreditLedger(self.store.home)
        ledger.reserve('owner', 'application:' + run['id'], date.today().isoformat(), 10, owner=True)
        apps.record_submission(run['id'], {'observed': True, 'confirmation_text': 'Application received', 'confirmation_url': 'https://example.com/thanks'})
        run_workspaces(self.store.home)
        self.assertEqual(ledger.history('owner')[0]['state'], 'charged')


class BrowserWorkerTests(unittest.TestCase):
    def test_missing_required_answers_pause_without_guessing(self):
        from jobpilot.worker import ApplicationWorker, TaskQueue
        from jobpilot.demo import make_server
        import threading
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0)
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            url = f'http://127.0.0.1:{server.server_port}'
            store = Store(directory)
            write_json(Path(directory) / 'profile.json', PROFILE)
            key = store.add({'url': url, 'company': 'Local Demo', 'title': 'React', 'description': 'React'})
            apps = Applications(store)
            run = apps.prepare(key, PROFILE, 'missing')
            apps.approve_documents(run['id'], run['document_hash'])
            TaskQueue(store).enqueue(run['id'], 'fill')
            adapter = {'origin': url, 'submit_selector': 'button[type=submit]', 'success_selector': '#receipt', 'success_text': 'Application received'}
            worker = ApplicationWorker(store, {url: adapter})
            worker.run_once()
            current = apps.get(run['id'])
            self.assertEqual(current['state'], 'NEEDS_USER')
            self.assertIn('Do you require sponsorship?', __import__('json').loads(current['form'])['unanswered_required'])
            self.assertFalse(current['form_approved'])
            self.assertFalse(current['receipt'] != '{}')

    def test_local_demo_fill_then_explicit_submission_receipt(self):
        from jobpilot.worker import ApplicationWorker, TaskQueue
        from jobpilot.demo import make_server
        import threading
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0)
            self.addCleanup(server.server_close)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            url = f'http://127.0.0.1:{server.server_port}'
            store = Store(directory)
            profile = __import__('copy').deepcopy(PROFILE)
            profile['answers'] = {'Do you require sponsorship?': 'No', 'I reviewed this demo application.': True}
            write_json(Path(directory) / 'profile.json', profile)
            key = store.add({'url': url, 'company': 'Local Demo', 'title': 'React', 'description': 'React'})
            apps = Applications(store)
            run = apps.prepare(key, profile, 'demo')
            apps.approve_documents(run['id'], run['document_hash'])
            adapter = {'origin': url, 'submit_selector': 'button[type=submit]', 'success_selector': '#receipt', 'success_text': 'Application received',
                       'fields': [{'selector': '#consent', 'kind': 'checkbox', 'profile_key': 'answers.I reviewed this demo application.'}]}
            # Dot-containing answer keys are deliberately not adapter path expressions.
            adapter['fields'][0]['profile_key'] = 'custom.consent'
            profile['custom'] = {'consent': True}
            write_json(Path(directory) / 'profile.json', profile)
            run = apps.prepare(key, profile, 'demo2')
            apps.approve_documents(run['id'], run['document_hash'])
            queue = TaskQueue(store)
            queue.enqueue(run['id'], 'fill')
            worker = ApplicationWorker(store, {url: adapter})
            worker.run_once()
            current = apps.get(run['id'])
            self.assertEqual(current['state'], 'FORM_REVIEW', current['error'])
            apps.approve_form(run['id'], current['form_hash'])
            queue.enqueue(run['id'], 'submit')
            worker.run_once()
            self.assertEqual(apps.get(run['id'])['state'], 'SUBMITTED')
            self.assertTrue(__import__('json').loads(apps.get(run['id'])['receipt'])['observed'])

    def test_automatic_lane_authorization_is_rechecked_before_submission(self):
        from jobpilot.worker import ApplicationWorker, TaskQueue
        from jobpilot.automation import Automation
        from jobpilot.billing import CreditLedger
        from jobpilot.demo import make_server
        import threading
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0)
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            url = f'http://127.0.0.1:{server.server_port}'
            store = Store(directory)
            profile = __import__('copy').deepcopy(PROFILE)
            profile['answers'] = {'Do you require sponsorship?': 'No'}
            profile['custom'] = {'consent': True}
            write_json(Path(directory) / 'profile.json', profile)
            store.add({'url': url, 'company': 'Local Demo', 'title': 'React', 'description': 'React'})
            adapter = {'origin': url, 'submit_selector': 'button[type=submit]', 'success_selector': '#receipt', 'success_text': 'Application received',
                       'fields': [{'selector': '#consent', 'kind': 'checkbox', 'profile_key': 'custom.consent'}]}
            ledger = CreditLedger(directory)
            automation = Automation(store, ledger, 'owner', owner=True, adapters={url: adapter})
            lane = automation.save_lane({'name': 'Automatic demo', 'mode': 'automatic'})
            automation.activate(lane['id'], True)
            identifier = automation.run()[0]
            worker = ApplicationWorker(store, {url: adapter}, ledger=ledger, account_id='owner', automation=automation)
            worker.run_once()
            self.assertEqual(Applications(store).get(identifier)['state'], 'APPROVED')
            automation.activate(lane['id'], False)
            worker.run_once()
            self.assertNotEqual(Applications(store).get(identifier)['state'], 'SUBMITTED')
            self.assertEqual(ledger.history('owner')[0]['state'], 'released')
