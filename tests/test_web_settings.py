import copy
import json
from unittest.mock import patch

from web_support import WebCase
from jobpilot.core import read_json, write_json
from jobpilot.service import ProcessLock
from test_workflow import PROFILE


class WebSettingsTests(WebCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_profile_draft_can_be_saved_but_not_claimed_verified(self):
        response = self.post('/profile', {'profile_json': json.dumps({'personal': {}, 'skills': []})}, page='/profile')
        self.assertIn(b'Profile saved', response.data)
        self.assertFalse(read_json(self.home / 'profile.json')['facts_verified'])
        response = self.post('/profile', {'profile_json': json.dumps(PROFILE)}, page='/profile')
        self.assertFalse(read_json(self.home / 'profile.json')['facts_verified'])

    def test_verified_profile_and_invalid_nested_edit(self):
        self.post('/profile', {'profile_json': json.dumps(PROFILE), 'facts_verified': 'yes'}, page='/profile')
        self.assertEqual(read_json(self.home / 'profile.json')['experience'], PROFILE['experience'])
        self.assertTrue(read_json(self.home / 'profile.json')['facts_verified'])
        invalid = copy.deepcopy(PROFILE)
        invalid['experience'] = ['bad shape']
        response = self.post('/profile', {'profile_json': json.dumps(invalid)}, page='/profile')
        self.assertIn(b'experience', response.data)
        self.assertEqual(read_json(self.home / 'profile.json')['experience'], PROFILE['experience'])

    def test_sources_validation_preserves_previous_config(self):
        config = {'sources': [{'type': 'greenhouse', 'board': 'example'}],
                  'title_keywords': ['react'], 'location_keywords': ['poland']}
        self.post('/sources', {'sources_json': json.dumps(config)}, page='/sources')
        invalid = {'sources': [{'type': 'custom', 'board': 'http://localhost'}]}
        self.post('/sources', {'sources_json': json.dumps(invalid)}, page='/sources')
        self.assertEqual(read_json(self.home / 'sources.json'), config)

    def test_discovery_reports_partial_errors_and_deduplicates(self):
        write_json(self.home / 'sources.json', {'sources': [{'type': 'greenhouse', 'board': 'example'}]})
        rows = [{'url': 'https://example.com/jobs/2', 'title': 'React engineer', 'company': 'Example',
                 'description': 'React', 'location': 'Poland', 'source': 'greenhouse'}]
        with patch('jobpilot.web_settings.discover', return_value=(rows, ['One source unavailable'])):
            self.post('/sources/discover', page='/sources')
            response = self.post('/sources/discover', page='/sources')
        self.assertIn(b'One source unavailable', response.data)
        self.assertEqual(len(self.app.extensions['jobpilot_store'].rows()), 1)

    def test_missing_sources_and_busy_discovery(self):
        response = self.post('/sources/discover', page='/sources')
        self.assertIn(b'Add', response.data)
        with ProcessLock(self.home / 'web-action.lock'):
            response = self.post('/sources/discover', page='/sources')
        self.assertIn(b'already running', response.data)

    def test_invalid_profile_preserves_unsaved_fields_and_raw_json(self):
        response = self.post('/profile', {
            'personal_first_name': 'Unsaved name', 'summary': 'Unsaved summary',
            'skills': 'React\nTypeScript', 'experience_json': '[broken',
        }, page='/profile')
        self.assertIn(b'Unsaved name', response.data)
        self.assertIn(b'Unsaved summary', response.data)
        self.assertIn(b'[broken', response.data)
        self.assertFalse((self.home / 'profile.json').exists())
