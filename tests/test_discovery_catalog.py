import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jobpilot.core import Store

class DiscoveryCatalogTests(unittest.TestCase):
    def test_ashby_normalizes_only_listed_jobs_and_supported_metadata(self):
        from jobpilot import sources
        self.assertTrue(hasattr(sources, 'ashby'), 'Ashby public feed is missing')
        data = {'jobs': [{'title':'Python Engineer','jobUrl':'https://jobs.ashbyhq.com/demo/one','location':'Poland','descriptionPlain':'Python SQL','isListed':True,'isRemote':True,'publishedAt':'2026-10-14T12:00:00Z','employmentType':'FullTime'}, {'title':'Hidden','isListed':False}]}
        with patch('jobpilot.sources.fetch_json', return_value=data):
            jobs = sources.ashby('demo')
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['workplace'], 'remote')
        self.assertEqual(jobs[0]['published_at'], '2026-10-14T12:00:00Z')

    def test_refresh_preserves_progress_and_separates_source_timestamps(self):
        from jobpilot import sources
        self.assertTrue(hasattr(sources, 'ashby'), 'Ashby public feed is missing')
        from jobpilot.discovery_catalog import ingest_jobs, catalog_rows
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            job = {'url':'https://jobs.ashbyhq.com/demo/one','title':'Engineer','company':'Demo','description':'Python','source':'ashby'}
            key = ingest_jobs(store, [job])[0]
            store.update(key, 'PREPARED', resume='original.pdf', note='Keep me')
            ingest_jobs(store, [dict(job, description='Python SQL')])
            row = catalog_rows(store)[0]
            self.assertEqual(row['status'], 'PREPARED')
            self.assertEqual(row['resume'], 'original.pdf')
            self.assertEqual(row['note'], 'Keep me')
            self.assertIsNone(row['published_at'])
            self.assertTrue(row['first_seen'])
            self.assertTrue(row['content_hash'])

    def test_filters_use_published_date_not_first_seen_and_explain_facts(self):
        from jobpilot import sources
        self.assertTrue(hasattr(sources, 'ashby'), 'Ashby public feed is missing')
        from jobpilot.discovery_catalog import filter_jobs, explain_match
        row = {'title':'Python Engineer','company':'Demo','location':'Poland','description':'Python SQL','workplace':'remote','published_at':None}
        self.assertEqual(filter_jobs([row], {'age':'24'}), [])
        self.assertEqual(filter_jobs([row], {'include':'python','exclude':'senior','location':'Poland','workplace':'remote'}), [row])
        self.assertEqual(filter_jobs([row], {'exclude':'Python'}), [])
        result = explain_match(row, {'skills':['Python','Rust']})
        self.assertEqual(result['matched_skills'], ['Python'])
        self.assertEqual(result['unmentioned_skills'], ['Rust'])
        self.assertIn('authorization', ' '.join(result['unknowns']).lower())

    def test_registry_and_url_reject_untrusted_destinations_before_network(self):
        from jobpilot import sources
        self.assertTrue(hasattr(sources, 'ashby'), 'Ashby public feed is missing')
        from jobpilot.discovery_catalog import normalize_sources, import_posting
        with self.assertRaises(ValueError): normalize_sources([{'type':'ashby','board':'../secret'}])
        for url in ['http://127.0.0.1/', 'https://jobs.ashbyhq.com.evil.test/demo/id', 'https://jobs.ashbyhq.com@127.0.0.1/demo/id', 'https://jobs.ashbyhq.com:444/demo/id']:
            with patch('jobpilot.sources.safe_fetch_json') as fetch:
                with self.assertRaises(ValueError): import_posting(url)
                fetch.assert_not_called()

    def test_import_selects_exact_job_and_private_dns_is_rejected(self):
        from jobpilot import sources
        self.assertTrue(hasattr(sources, 'ashby'), 'Ashby public feed is missing')
        from jobpilot.discovery_catalog import import_posting
        with patch('jobpilot.sources.safe_fetch_json', return_value={'jobs':[{'title':'Engineer','jobUrl':'https://jobs.ashbyhq.com/demo/one','isListed':True}]}):
            self.assertEqual(import_posting('https://jobs.ashbyhq.com/demo/one/apply')['title'], 'Engineer')
        with patch('jobpilot.sources.socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): sources.safe_fetch_json('https://api.ashbyhq.com/posting-api/job-board/demo')

    def test_feed_transport_refuses_redirects_and_bounds_bytes(self):
        from jobpilot import sources
        from unittest.mock import MagicMock
        response = MagicMock(status=302)
        connection = MagicMock()
        connection.getresponse.return_value = response
        public_dns = [(2,1,6,'',('8.8.8.8',443))]
        with patch('jobpilot.sources.socket.getaddrinfo',return_value=public_dns), patch('jobpilot.sources.socket.create_connection'), patch('jobpilot.sources.ssl.create_default_context'), patch('jobpilot.sources.http.client.HTTPSConnection',return_value=connection):
            with self.assertRaisesRegex(ValueError, 'redirects'):
                sources.safe_fetch_json('https://api.ashbyhq.com/posting-api/job-board/demo')
            response.status = 200
            response.read1.return_value = b'x' * 101
            token = sources._budget.set(sources.DiscoveryBudget(max_bytes=100))
            try:
                with self.assertRaisesRegex(ValueError, 'response limit'):
                    sources.safe_fetch_json('https://api.ashbyhq.com/posting-api/job-board/demo')
            finally: sources._budget.reset(token)

    def test_default_company_budget_is_reported(self):
        from jobpilot.sources import discover
        with patch('jobpilot.sources.greenhouse',return_value=[]), patch('jobpilot.sources.time.sleep'):
            jobs, errors = discover({'sources':[{'type':'greenhouse','board':'demo'}]*21})
        self.assertTrue(any('Company limit' in error for error in errors))

    def test_transport_reads_response_when_server_closes_connection(self):
        from jobpilot import sources
        from unittest.mock import MagicMock
        connection, response = MagicMock(), MagicMock(status=200)
        response.read1.side_effect = [b'{"jobs":[]}', b'']
        def headers():
            connection.sock = None
            return response
        connection.getresponse.side_effect = headers
        with patch('jobpilot.sources.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]), patch('jobpilot.sources.socket.create_connection'), patch('jobpilot.sources.ssl.create_default_context'), patch('jobpilot.sources.http.client.HTTPSConnection',return_value=connection):
            self.assertEqual(sources.safe_fetch_json('https://api.ashbyhq.com/posting-api/job-board/demo'), {'jobs':[]})

    def test_dns_lookup_is_bounded_by_discovery_deadline(self):
        from jobpilot import sources
        self.assertTrue(hasattr(sources, '_resolve_public'), 'Bounded DNS resolver is missing')
        import threading
        blocked = threading.Event()
        with patch('jobpilot.sources.socket.getaddrinfo', side_effect=lambda *args, **kwargs: blocked.wait(1)):
            try:
                with self.assertRaises(TimeoutError):
                    sources._resolve_public('api.ashbyhq.com', sources.DiscoveryBudget(seconds=.01))
            finally: blocked.set()

    def test_pass_cannot_overwrite_concurrently_started_application(self):
        from jobpilot.discovery_catalog import ingest_jobs, set_preference
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            key = ingest_jobs(store, [{'url':'https://example.com/job','title':'Engineer','company':'Demo'}])[0]
            original_get = store.get
            def stale_get(identifier):
                stale = original_get(identifier)
                with store.connect() as connection:
                    connection.execute("UPDATE jobs SET status='SUBMITTED' WHERE id=?", (key,))
                return stale
            with patch.object(store, 'get', side_effect=stale_get):
                with self.assertRaises(ValueError): set_preference(store, key, 'skip')
            self.assertEqual(original_get(key)['status'], 'SUBMITTED')
