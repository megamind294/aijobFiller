import io
import json
import unittest
from unittest.mock import patch


class DiscoveryLimitTests(unittest.TestCase):
    def test_full_lever_pages_stop_at_limit_with_partial_results(self):
        from jobpilot.sources import DiscoveryBudget, discover
        rows = [{'id': str(i), 'text': 'React Engineer', 'hostedUrl': f'https://example.com/{i}',
                 'descriptionPlain': 'React', 'categories': {'location': 'Poland'}} for i in range(100)]
        with patch('jobpilot.sources.fetch_json', return_value=rows):
            found, errors = discover({'sources': [{'type': 'lever', 'board': 'demo'}]},
                                     budget=DiscoveryBudget(seconds=30, max_pages=2, max_jobs=500))
        self.assertEqual(len(found), 200)
        self.assertTrue(any('page limit' in error for error in errors))

    def test_expired_budget_stops_before_fetch(self):
        from jobpilot.sources import DiscoveryBudget, discover
        found, errors = discover({'sources': [{'type': 'greenhouse', 'board': 'demo'}]},
                                 budget=DiscoveryBudget(seconds=-1))
        self.assertEqual(found, [])
        self.assertTrue(any('time limit' in error for error in errors))

    def test_large_response_is_rejected(self):
        from jobpilot.sources import DiscoveryBudget, discover
        response = io.BytesIO(json.dumps({'jobs': [], 'padding': 'x' * 1000}).encode())
        with patch('jobpilot.sources.urlopen', return_value=response):
            found, errors = discover({'sources': [{'type': 'greenhouse', 'board': 'demo'}]},
                                     budget=DiscoveryBudget(max_bytes=100))
        self.assertEqual(found, [])
        self.assertTrue(any('response limit' in error for error in errors))
