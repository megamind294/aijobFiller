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

    def test_failed_web_reservation_preserves_form_review_for_retry(self):
        from flask import Flask, g
        from jobpilot.billing import CreditLedger
        from jobpilot.web_applications import approve_and_queue
        from jobpilot.automation import Automation
        write_json(self.store.home / 'profile.json', PROFILE)
        key = self.store.add({'url': 'http://127.0.0.1:1', 'title': 'React', 'company': 'Demo', 'description': 'React'})
        apps = Applications(self.store)
        run = apps.prepare(key, PROFILE, 'budget-failure')
        apps.approve_documents(run['id'], run['document_hash'])
        apps.record_form(run['id'], {'fields': {'Name': 'Demo'}, 'unanswered_required': []})
        ledger = CreditLedger(self.store.home)
        limits = Automation(self.store, ledger, 'owner', owner=True)
        limits.settings(daily_cap=1)
        ledger.reserve('owner', 'occupied', limits.preview()['day'], 1, owner=True)
        app = Flask(__name__)
        app.config['JOBPILOT_HOME'] = str(self.store.home)
        app.extensions['jobpilot_credit_ledger'] = ledger
        with app.test_request_context():
            g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
            with self.assertRaisesRegex(ValueError, 'cap'):
                approve_and_queue(run['id'], apps.get(run['id'])['form_hash'], 'form')
            self.assertEqual(apps.get(run['id'])['state'], 'FORM_REVIEW')
            self.assertFalse(apps.get(run['id'])['form_approved'])
            self.assertEqual(self.queue.rows(), [])
            ledger.settle('owner', 'occupied', False)
            approve_and_queue(run['id'], apps.get(run['id'])['form_hash'], 'form')
        self.assertEqual(apps.get(run['id'])['state'], 'APPROVED')
        self.assertEqual(self.queue.rows()[0]['state'], 'QUEUED')

    def test_changing_automatic_lane_mode_does_not_authorize_old_submission(self):
        from jobpilot.automation import Automation
        from jobpilot.billing import CreditLedger
        from jobpilot.worker import ApplicationWorker
        write_json(self.store.home / 'profile.json', PROFILE)
        key = self.store.add({'url': 'http://127.0.0.1:1', 'title': 'React', 'company': 'Demo', 'description': 'React'})
        ledger = CreditLedger(self.store.home)
        automation = Automation(self.store, ledger, 'owner', owner=True,
                                adapters={'http://127.0.0.1:1': {'origin': 'http://127.0.0.1:1'}})
        lane = automation.save_lane({'name': 'Automatic', 'mode': 'automatic'})
        automation.activate(lane['id'], True)
        identifier = automation.run()[0]
        apps = Applications(self.store)
        task = self.queue.claim('fixture')
        apps.record_form(identifier, {'fields': {'Name': 'Demo'}, 'unanswered_required': []})
        apps.approve_form(identifier, apps.get(identifier)['form_hash'])
        self.queue.complete(task['id'], 'fixture')
        self.queue.enqueue(identifier, 'submit')
        automation.save_lane({'name': 'Review', 'mode': 'review-first'}, lane['id'])
        worker = ApplicationWorker(self.store, automation.adapters, ledger=ledger, account_id='owner', automation=automation)
        worker.run_once()
        self.assertEqual(apps.get(identifier)['state'], 'NEEDS_USER')
        self.assertIn('authorization was revoked', apps.get(identifier)['error'])
        self.assertEqual(apps.get(identifier)['receipt'], '{}')
        self.assertFalse(any(row['attempted'] for row in self.queue.rows()))

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

    def test_worker_releases_manual_report_reservation_after_route_crash(self):
        from jobpilot.worker import run_workspaces
        from jobpilot.billing import CreditLedger
        write_json(self.store.home / 'profile.json', PROFILE)
        key = self.store.add({'url': 'https://example.com/job', 'title': 'React', 'company': 'Example', 'description': 'React'})
        apps = Applications(self.store)
        run = apps.prepare(key, PROFILE, 'manual-credit-reconcile')
        ledger = CreditLedger(self.store.home)
        ledger.reserve('owner', 'application:' + run['id'], '2026-10-16', 10, owner=True)
        apps.record_manual_submission(run['id'], 'Employer confirmation reference ABC-123', True, run['document_hash'])
        self.assertEqual(ledger.history('owner')[0]['state'], 'reserved')
        run_workspaces(self.store.home)
        self.assertEqual(ledger.history('owner')[0]['state'], 'released')
        self.assertEqual(apps.get(run['id'])['receipt'], '{}')


class BrowserWorkerTests(unittest.TestCase):
    def test_review_first_lane_explicit_web_approvals_reach_local_receipt(self):
        from flask import Flask, g
        from jobpilot.worker import ApplicationWorker, TaskQueue
        from jobpilot.automation import Automation
        from jobpilot.billing import CreditLedger
        from jobpilot.demo import make_server
        from jobpilot.web_applications import approve_and_queue
        import copy
        import json
        import threading
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0)
            self.addCleanup(server.server_close)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            url = f'http://127.0.0.1:{server.server_port}'
            store = Store(directory)
            profile = copy.deepcopy(PROFILE)
            profile['answers'] = {'Do you require sponsorship?': 'No'}
            profile['custom'] = {'consent': True}
            write_json(Path(directory) / 'profile.json', profile)
            job_id = store.add({'url': url, 'company': 'Local Demo', 'title': 'React', 'description': 'React'})
            adapter = {'origin': url, 'submit_selector': 'button[type=submit]', 'success_selector': '#receipt',
                       'success_text': 'Application received',
                       'fields': [{'selector': '#consent', 'kind': 'checkbox', 'profile_key': 'custom.consent'}]}
            ledger = CreditLedger(directory)
            automation = Automation(store, ledger, 'owner', owner=True, adapters={url: adapter})
            lane = automation.save_lane({'name': 'Review demo', 'mode': 'review-first'})
            automation.activate(lane['id'], True)
            identifier = automation.run()[0]
            apps, queue = Applications(store), TaskQueue(store)
            self.assertEqual(apps.get(identifier)['state'], 'DOCUMENT_REVIEW')
            self.assertEqual(queue.rows(), [])
            app = Flask(__name__)
            app.config['JOBPILOT_HOME'] = directory
            app.extensions['jobpilot_credit_ledger'] = ledger
            with app.test_request_context():
                g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
                approve_and_queue(identifier, apps.get(identifier)['document_hash'], 'documents')
            worker = ApplicationWorker(store, {url: adapter}, ledger=ledger, account_id='owner', automation=automation)
            worker.run_once()
            self.assertEqual(apps.get(identifier)['state'], 'FORM_REVIEW')
            self.assertEqual(apps.get(identifier)['receipt'], '{}')
            self.assertFalse(worker.run_once(), 'Review-first must wait for explicit form approval')
            with app.test_request_context():
                g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
                approve_and_queue(identifier, apps.get(identifier)['form_hash'], 'form')
            worker.run_once()
            self.assertEqual(apps.get(identifier)['state'], 'SUBMITTED', apps.get(identifier)['error'])
            self.assertTrue(json.loads(apps.get(identifier)['receipt'])['observed'])
            self.assertEqual(store.get(job_id)['status'], 'SUBMITTED')
            self.assertEqual(ledger.history('owner')[0]['state'], 'charged')

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
