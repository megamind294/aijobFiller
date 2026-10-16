import unittest
from unittest.mock import patch
from tests.web_support import WebCase

class BrowseTests(WebCase):
    def test_search_fetches_real_sources_for_new_workspace_and_caches_refresh(self):
        self.login()
        posting = {'title':'Senior Front-End Engineer','company':'Example',
                   'url':'https://jobs.ashbyhq.com/example/one','source':'ashby',
                   'description':'Build React interfaces','location':'Remote'}
        with patch('jobpilot.web_discovery.discover', return_value=([posting], [])) as fetch:
            result = self.post('/browse/search', {'q':'frontend developer'}, page='/browse')
            self.assertEqual(result.status_code, 200)
            self.assertIn('Senior Front-End Engineer', result.get_data(as_text=True))
            self.assertTrue(fetch.call_args.args[0]['sources'])
            self.assertTrue(fetch.call_args.kwargs['safe_transport'])
            self.post('/browse/search', {'q':'React'}, page='/browse')
            self.assertEqual(fetch.call_count, 1)

    def test_search_reports_provider_failure_instead_of_silent_empty_results(self):
        self.login()
        with patch('jobpilot.web_discovery.discover', return_value=([], ['example: TimeoutError'])):
            result = self.post('/browse/search', {'q':'frontend developer'}, page='/browse')
        self.assertIn('could not be refreshed', result.get_data(as_text=True))

    def setUp(self):
        super().setUp()
        if 'browse' not in self.app.view_functions:
            try:
                from jobpilot.web_discovery import register_discovery
                register_discovery(self.app)
            except ImportError:
                pass

    def test_browse_requires_login_and_has_real_empty_state(self):
        self.assertEqual(self.client.get('/browse', base_url='https://localhost').status_code, 302)
        self.login()
        result = self.client.get('/browse', base_url='https://localhost')
        self.assertEqual(result.status_code, 200)
        self.assertIn('No opportunities match', result.get_data(as_text=True))

    def test_import_manual_save_skip_and_detail_escape_content(self):
        self.login()
        self.assertIn('browse', self.app.view_functions, 'Browse routes are missing')
        response = self.post('/browse/import-url', {'mode':'manual','url':'https://example.org/job','title':'Engineer <script>','company':'Demo','description':'Python <script>alert(1)</script>'}, page='/browse')
        self.assertEqual(response.status_code, 200)
        from jobpilot.core import Store
        store = Store(self.home)
        key = store.rows()[0]['id']
        detail = self.client.get('/browse/' + key, base_url='https://localhost').get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>', detail)
        self.post('/browse/' + key + '/action', {'action':'save'}, page='/browse')
        self.assertEqual(store.get(key)['status'], 'DISCOVERED')
        self.post('/browse/' + key + '/action', {'action':'skip'}, page='/browse')
        self.assertEqual(store.get(key)['status'], 'SKIPPED')
        store.update(key, 'SUBMITTED', note='receipt')
        self.post('/browse/' + key + '/action', {'action':'skip'}, page='/browse')
        self.assertEqual(store.get(key)['status'], 'SUBMITTED')

    def test_discovery_uses_registry_normal_form_and_reports_missing_source_date(self):
        self.login()
        self.assertIn('browse', self.app.view_functions, 'Browse routes are missing')
        with patch('jobpilot.web_discovery.discover', return_value=([{'title':'Engineer','company':'Demo','url':'https://jobs.ashbyhq.com/demo/one','source':'ashby','description':'Python'}], [])) as fetch:
            result = self.post('/browse/discover', {'source_type':'ashby','board':'demo','company':'Demo','region':'global'}, page='/browse')
        self.assertEqual(fetch.call_args.kwargs['safe_transport'], True)
        self.assertIn('Published date unavailable', result.get_data(as_text=True))
        self.assertTrue((self.home / 'sources.json').exists())

    def test_unsupported_url_offers_manual_without_request(self):
        self.login()
        self.assertIn('browse', self.app.view_functions, 'Browse routes are missing')
        with patch('jobpilot.sources.safe_fetch_json') as fetch:
            result = self.post('/browse/import-url', {'url':'https://127.0.0.1/secret'}, page='/browse')
        fetch.assert_not_called()
        self.assertIn('manually', result.get_data(as_text=True))

    def test_registry_refresh_preserves_existing_source_preferences(self):
        from jobpilot.core import write_json, read_json
        self.login()
        write_json(self.home / 'sources.json', {'sources':[], 'title_keywords':['React'], 'location_keywords':['Poland']})
        with patch('jobpilot.web_discovery.discover', return_value=([], [])):
            self.post('/browse/discover', {'source_type':'ashby','board':'demo'}, page='/browse')
        self.assertEqual(read_json(self.home / 'sources.json')['title_keywords'], ['React'])

    def test_detail_does_not_read_another_account_job(self):
        from flask import g
        from jobpilot.workspace import current_store
        with self.app.test_request_context('/'):
            g.jobpilot_account = {'id':'a'*32, 'role':'customer'}
            key = current_store().add({'url':'https://example.com/private','title':'Private customer role','company':'Private'})
        self.login()
        self.assertEqual(self.client.get('/browse/' + key, base_url='https://localhost').status_code, 404)
