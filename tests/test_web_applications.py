import tempfile
import unittest
from pathlib import Path
from flask import Flask, g
from jobpilot.core import Store, write_json
from jobpilot.applications import Applications
from test_workflow import PROFILE


class ApplicationWebTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.web_applications import register_applications
        from jobpilot.web_automation import register_automation
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / 'jobpilot' / 'web_templates'), static_folder=str(Path(__file__).parents[1] / 'jobpilot' / 'web_static'))
        app.config.update(TESTING=True, SECRET_KEY='test', JOBPILOT_HOME=str(self.root))
        app.jinja_env.globals['csrf_token'] = lambda: 'test'
        @app.before_request
        def account():
            g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
        register_applications(app)
        register_automation(app)
        self.client = app.test_client()
        store = Store(self.root)
        write_json(self.root / 'profile.json', PROFILE)
        self.job = store.add({'url': 'https://example.com/job', 'title': 'React', 'company': 'Example', 'description': 'React'})

    def test_prepare_review_exact_download_and_missing_receipt(self):
        response = self.client.post('/applications/prepare/' + self.job, data={'request_key': 'web'})
        self.assertEqual(response.status_code, 302)
        review = self.client.get(response.location)
        self.assertEqual(review.status_code, 200)
        self.assertIn(b'Exact employer attachments', review.data)
        identifier = response.location.rsplit('/', 1)[1]
        download = self.client.get('/applications/' + identifier + '/artifacts/resume')
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith(b'%PDF'))
        download.close()
        self.assertEqual(self.client.post('/applications/' + identifier + '/submit', data={'form_hash': 'fake'}).status_code, 400)

    def test_automation_surface_is_default_off(self):
        response = self.client.get('/auto-apply')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'On demand', response.data)
        self.assertIn(b'Create lane', response.data)

    def test_unknown_package_is_not_found(self):
        self.assertEqual(self.client.get('/applications/' + 'a' * 32).status_code, 404)

    def test_paused_unattempted_submission_can_start_fresh_review_only(self):
        from jobpilot.worker import TaskQueue
        from jobpilot.billing import CreditLedger
        store = Store(self.root)
        apps = Applications(store)
        run = apps.prepare(self.job, PROFILE, 'changed-form')
        apps.approve_documents(run['id'], run['document_hash'])
        apps.record_form(run['id'], {'fields': {'name': 'Demo'}, 'unanswered_required': []})
        apps.approve_form(run['id'], apps.get(run['id'])['form_hash'])
        queue = TaskQueue(store)
        queue.enqueue(run['id'], 'submit')
        task = queue.claim('worker')
        apps.record_form(run['id'], {'fields': {'name': 'Changed live field'}, 'unanswered_required': []})
        apps.fail(run['id'], 'Live form changed; no submission attempted.')
        queue.complete(task['id'], 'worker', 'PAUSED')
        ledger = CreditLedger(self.root)
        ledger.reserve('owner', 'application:' + run['id'], '2026-10-15', 10, owner=True)
        ledger.settle('owner', 'application:' + run['id'], False)
        review = self.client.get('/applications/' + run['id'])
        self.assertIn(b'Prepare a new review; previous attempt was not submitted', review.data)
        response = self.client.post('/applications/' + run['id'] + '/restart-review', data={'request_key': 'fresh-after-change'})
        self.assertEqual(response.status_code, 302)
        fresh = apps.get(response.location.rsplit('/', 1)[1])
        self.assertNotEqual(fresh['id'], run['id'])
        self.assertEqual(fresh['state'], 'DOCUMENT_REVIEW')
        self.assertFalse(fresh['document_approved'])
        self.assertFalse(fresh['form_approved'])
        self.assertEqual(len(queue.rows()), 1)
        self.assertEqual(queue.rows()[0]['state'], 'PAUSED')

    def test_attempted_submission_cannot_restart_review(self):
        store = Store(self.root)
        apps = Applications(store)
        run = apps.prepare(self.job, PROFILE, 'uncertain')
        apps.fail(run['id'], 'Receipt unknown', uncertain=True)
        review = self.client.get('/applications/' + run['id'])
        self.assertNotIn(b'Prepare a new review; previous attempt was not submitted', review.data)
        response = self.client.post('/applications/' + run['id'] + '/restart-review', data={'request_key': 'unsafe-retry'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(apps.rows()), 1)

    def test_manual_handoff_requires_attestation_then_records_and_releases_credit(self):
        from jobpilot.billing import CreditLedger
        store = Store(self.root)
        apps = Applications(store)
        run = apps.prepare(self.job, PROFILE, 'manual-web')
        apps.fail(run['id'], 'Unsupported employer form.')
        ledger = CreditLedger(self.root)
        ledger.reserve('owner', 'application:' + run['id'], '2026-10-16', 10, owner=True)
        review = self.client.get('/applications/' + run['id'])
        self.assertIn(b'Record your submission', review.data)
        data = {'document_hash': run['document_hash'], 'evidence_note': 'Employer portal reference ABC-123'}
        response = self.client.post('/applications/' + run['id'] + '/manual-report', data=data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(apps.get(run['id'])['state'], 'NEEDS_USER')
        data['attested'] = 'yes'
        response = self.client.post('/applications/' + run['id'] + '/manual-report', data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ledger.history('owner')[0]['state'], 'released')
        review = self.client.get(response.location)
        self.assertIn(b'User-reported submission', review.data)
        self.assertIn(b'Employer portal reference ABC-123', review.data)
        self.assertNotIn(b'Observed employer receipt', review.data)
        self.assertNotIn(b'Record your submission', review.data)
        self.assertEqual(apps.get(run['id'])['receipt'], '{}')
        self.assertEqual(self.client.post('/applications/' + run['id'] + '/manual-report', data=data).status_code, 302)
        data['evidence_note'] = 'Different evidence overwrite attempt'
        self.assertEqual(self.client.post('/applications/' + run['id'] + '/manual-report', data=data).status_code, 400)

    def test_manual_reconciliation_does_not_refund_charged_credit_or_queue_retry(self):
        from jobpilot.billing import CreditLedger
        from jobpilot.worker import TaskQueue
        apps = Applications(Store(self.root))
        run = apps.prepare(self.job, PROFILE, 'uncertain-web-manual')
        apps.fail(run['id'], 'Submission receipt unknown', uncertain=True)
        ledger = CreditLedger(self.root)
        ledger.reserve('owner', 'application:' + run['id'], '2026-10-16', 10, owner=True)
        ledger.settle('owner', 'application:' + run['id'], True)
        response = self.client.post('/applications/' + run['id'] + '/manual-report', data={
            'attested': 'yes', 'document_hash': run['document_hash'], 'evidence_note': 'Verified my portal history REF-456'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ledger.history('owner')[0]['state'], 'charged')
        self.assertEqual(TaskQueue(Store(self.root)).rows(), [])
        self.assertEqual(apps.get(run['id'])['state'], 'SUBMITTED')

    def test_active_worker_hides_and_rejects_manual_reporting(self):
        from jobpilot.worker import TaskQueue
        store = Store(self.root)
        apps = Applications(store)
        run = apps.prepare(self.job, PROFILE, 'active-manual-web')
        TaskQueue(store).enqueue(run['id'], 'fill')
        review = self.client.get('/applications/' + run['id'])
        self.assertNotIn(b'Record your submission', review.data)
        response = self.client.post('/applications/' + run['id'] + '/manual-report', data={
            'attested': 'yes', 'document_hash': run['document_hash'], 'evidence_note': 'Portal confirmation ABC-123'})
        self.assertEqual(response.status_code, 400)

    def test_ai_web_preparation_requires_consent_and_paused_review_reuses_draft(self):
        import json
        from unittest.mock import patch
        from jobpilot.worker import TaskQueue
        self.client.application.config['GEMINI_API_KEY'] = 'test-fixture'
        apps = Applications(Store(self.root))
        response = self.client.post('/applications/prepare/' + self.job, data={
            'request_key': 'web-ai', 'optimization': 'ai'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(apps.rows(), [])
        generated = {'summary': 'A frontend developer.', 'experience': [
            {'index': 0, 'bullets': [{'index': 0, 'text': 'Developed React forms.'}]}], 'projects': []}
        with patch('jobpilot.assistant.provider_reply', return_value=json.dumps(generated)):
            response = self.client.post('/applications/prepare/' + self.job, data={
                'request_key': 'web-ai', 'optimization': 'ai', 'ai_consent': 'yes'})
        self.assertEqual(response.status_code, 302)
        identifier = response.location.rsplit('/', 1)[1]
        source = apps.snapshot(identifier)
        queue = TaskQueue(Store(self.root))
        queue.enqueue(identifier, 'fill')
        task = queue.claim('fixture')
        queue.complete(task['id'], 'fixture', 'PAUSED')
        apps.fail(identifier, 'Unknown required answer')
        with patch('jobpilot.assistant.provider_reply', side_effect=AssertionError('Restart must reuse reviewed AI text')):
            response = self.client.post('/applications/' + identifier + '/restart-review', data={'request_key': 'ai-restart'})
        self.assertEqual(response.status_code, 302)
        fresh = apps.get(response.location.rsplit('/', 1)[1])
        self.assertEqual(fresh['state'], 'DOCUMENT_REVIEW')
        self.assertFalse(fresh['document_approved'])
        self.assertEqual(apps.snapshot(fresh['id'])['prepared_profile'], source['prepared_profile'])
