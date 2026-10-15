import unittest
from flask import g
from web_support import WebCase


class WorkspaceTests(WebCase):
    def test_owner_keeps_existing_home(self):
        from jobpilot.workspace import current_home
        with self.app.test_request_context('/'):
            g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
            self.assertEqual(current_home(), self.home.resolve())

    def test_customer_homes_and_stores_are_isolated(self):
        from jobpilot.workspace import current_home, current_store
        with self.app.test_request_context('/'):
            g.jobpilot_account = {'id': 'a' * 32, 'role': 'customer'}
            first_home = current_home()
            current_store().add({'url': 'https://example.com/job', 'title': 'Private role', 'company': 'Example'})
        with self.app.test_request_context('/'):
            g.jobpilot_account = {'id': 'b' * 32, 'role': 'customer'}
            self.assertNotEqual(first_home, current_home())
            self.assertEqual(current_store().rows(), [])

    def test_account_identifier_cannot_escape_workspace(self):
        from jobpilot.workspace import current_home
        with self.app.test_request_context('/'):
            g.jobpilot_account = {'id': '../../private', 'role': 'customer'}
            with self.assertRaises(ValueError):
                current_home()
