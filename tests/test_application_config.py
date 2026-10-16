import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jobpilot.core import write_json


class ApplicationConfigTests(unittest.TestCase):
    def test_web_and_worker_use_same_operator_adapter_file(self):
        from flask import Flask, g
        from jobpilot.application_config import load_worker_config
        from jobpilot.web_automation import current_automation
        with tempfile.TemporaryDirectory() as directory:
            adapter = {'origin': 'https://example.com', 'fields': [], 'submit_selector': '#send',
                       'success_selector': '#receipt', 'success_text': 'Application received'}
            path = Path(directory) / 'operator-adapters.json'
            write_json(path, {'adapters': {'https://example.com': adapter}})
            app = Flask(__name__)
            app.config['JOBPILOT_HOME'] = directory
            with patch.dict(os.environ, {'JOBPILOT_APPLICATION_ADAPTERS_FILE': str(path)}):
                config = load_worker_config(directory)
                with app.test_request_context():
                    g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
                    self.assertEqual(current_automation().adapters, config['adapters'])
                    self.assertEqual(current_automation().adapters['https://example.com']['submit_selector'], '#send')

    def test_missing_file_is_empty_and_invalid_file_fails_clearly(self):
        from jobpilot.application_config import load_worker_config
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.json'
            with patch.dict(os.environ, {'JOBPILOT_APPLICATION_ADAPTERS_FILE': str(path)}):
                self.assertEqual(load_worker_config(directory)['adapters'], {})
                path.write_text('{invalid', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'configuration'):
                    load_worker_config(directory)

    def test_explicit_flask_override_and_origin_validation(self):
        from jobpilot.application_config import load_worker_config
        with tempfile.TemporaryDirectory() as directory:
            adapter = {'origin': 'https://example.com'}
            config = load_worker_config(directory, {'JOBPILOT_APPLICATION_ADAPTERS': {'https://example.com': adapter}})
            self.assertEqual(config['adapters'], {'https://example.com': adapter})
            with self.assertRaises(ValueError):
                load_worker_config(directory, {'JOBPILOT_APPLICATION_ADAPTERS': {'https://example.com': {'origin': 'https://other.com'}}})
