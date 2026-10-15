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
