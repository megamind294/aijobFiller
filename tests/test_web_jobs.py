import io
import json

from web_support import WebCase
from jobpilot.core import read_json, write_json
from test_workflow import PROFILE


class WebJobsTests(WebCase):
    def setUp(self):
        super().setUp()
        self.login()
        self.store = self.app.extensions['jobpilot_store']

    def add_job(self, **values):
        return self.store.add(dict(url='https://example.com/jobs/42', title='Frontend Developer',
                                   company='Example', description='React TypeScript', location='Poland', **values))

    def test_empty_queue_manual_add_and_search(self):
        self.assertIn(b'No opportunities yet', self.client.get('/jobs').data)
        response = self.post('/jobs/add', {'url': 'https://example.com/jobs/42', 'title': 'Frontend Developer',
                                         'company': 'Example', 'description': '<script>alert(1)</script>'})
        self.assertIn(b'Opportunity added', response.data)
        self.assertIn(b'Frontend Developer', self.client.get('/jobs?q=Frontend').data)
        self.assertNotIn(b'Frontend Developer', self.client.get('/jobs?q=doesnotexist').data)
        job = self.store.rows()[0]
        self.assertIn(b'&lt;script&gt;', self.client.get('/jobs/' + job['id']).data)
        self.assertNotIn(b'<script>alert', self.client.get('/jobs/' + job['id']).data)

    def test_import_validates_all_rows_before_writing(self):
        jobs = [{'url': 'https://example.com/a', 'title': 'A', 'company': 'B'},
                {'url': 'javascript:alert(1)', 'title': 'C', 'company': 'D'}]
        response = self.post('/jobs/import', {'file': (io.BytesIO(json.dumps(jobs).encode()), 'jobs.json')})
        self.assertEqual(len(self.store.rows()), 0)
        self.assertIn(b'http(s)', response.data)

    def test_duplicate_import_preserves_application_history(self):
        key = self.add_job()
        self.store.update(key, 'SUBMITTED', note='Verified receipt')
        self.post('/jobs/import', {'file': (io.BytesIO(json.dumps([{
            'url': 'https://example.com/jobs/42', 'title': 'New title', 'company': 'Example'}]).encode()), 'jobs.json')})
        self.assertEqual(self.store.get(key)['status'], 'SUBMITTED')
        self.assertEqual(self.store.get(key)['title'], 'New title')
        self.assertIn(b'Verified receipt', self.client.get('/jobs/' + key).data)

    def test_status_changes_require_note_and_confirmation(self):
        key = self.add_job()
        self.post('/jobs/' + key + '/status', {'status': 'SUBMITTED', 'note': 'Receipt checked'})
        self.assertEqual(self.store.get(key)['status'], 'DISCOVERED')
        self.post('/jobs/' + key + '/status', {'status': 'SUBMITTED', 'note': '', 'confirmed': 'yes'})
        self.assertEqual(self.store.get(key)['status'], 'DISCOVERED')
        self.post('/jobs/' + key + '/status', {'status': 'SUBMITTED', 'note': 'Receipt checked', 'confirmed': 'yes'})
        self.assertEqual(self.store.get(key)['status'], 'SUBMITTED')
        self.assertIn(b'Receipt checked', self.client.get('/jobs/' + key).data)

    def test_prepare_and_download_use_verified_facts(self):
        key = self.add_job()
        response = self.post('/jobs/' + key + '/prepare')
        self.assertIn(b'profile', response.data.lower())
        self.assertEqual(self.store.get(key)['status'], 'DISCOVERED')
        write_json(self.home / 'profile.json', PROFILE)
        self.post('/jobs/' + key + '/prepare')
        response = self.client.get('/jobs/' + key + '/resume')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'%PDF'))
        self.assertEqual(read_json(self.home / 'resumes' / key / 'facts.json')['experience'], PROFILE['experience'])
        response = self.client.get('/export')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'PK'))

    def test_download_rejects_external_resume_path(self):
        key = self.add_job()
        private = self.home.parent / (self.home.name + '-private.txt')
        private.write_text('do not disclose')
        self.addCleanup(private.unlink)
        self.store.update(key, 'PREPARED', resume=str(private))
        response = self.client.get('/jobs/' + key + '/resume')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'do not disclose', response.data)
        self.assertEqual(self.client.get('/jobs/unknown').status_code, 404)

    def test_upload_limit(self):
        from werkzeug.test import EnvironBuilder
        builder = EnvironBuilder(path='/jobs/import', method='POST', base_url='https://localhost',
                                 headers={'Referer': 'https://localhost/'}, data={
                                     'csrf_token': self.csrf(path='/'),
                                     'file': (io.BytesIO(b'x' * (5 * 1024 * 1024 + 1)), 'jobs.json')})
        environment = builder.get_environ()
        try:
            response = self.client.open(environment)
            self.assertEqual(response.status_code, 413)
        finally:
            environment['wsgi.input'].close()
            builder.close()

    def test_busy_action_does_not_prepare(self):
        from jobpilot.service import ProcessLock
        key = self.add_job()
        write_json(self.home / 'profile.json', PROFILE)
        with ProcessLock(self.home / 'web-action.lock'):
            response = self.post('/jobs/' + key + '/prepare')
        self.assertIn(b'already running', response.data)
        self.assertEqual(self.store.get(key)['status'], 'DISCOVERED')
