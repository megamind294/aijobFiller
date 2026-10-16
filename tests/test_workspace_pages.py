from web_support import WebCase


class WorkspacePageTests(WebCase):
    def test_contact_edit_archive_restore_persists_and_does_not_send(self):
        from jobpilot.core import read_json
        self.post('/networking', {'name':'Recruiter Example','company':'Example','email':'recruiter@example.test'}, page='/networking')
        contact = read_json(self.home / 'networking.json')[0]
        route = '/networking/' + contact['id'] + '/update'
        result = self.post(route, {'name':'Updated Recruiter','company':'Example','email':'recruiter@example.test','notes':'Updated notes','draft':'Private draft'}, page='/networking')
        self.assertIn(b'Updated notes', result.data)
        self.assertEqual(read_json(self.home / 'networking.json')[0]['draft'], 'Private draft')
        self.post(route, {'action':'archive'}, page='/networking')
        self.assertTrue(read_json(self.home / 'networking.json')[0]['archived'])
        self.post(route, {'action':'restore'}, page='/networking')
        self.assertFalse(read_json(self.home / 'networking.json')[0]['archived'])
        self.assertIn(b'No message', result.data)

    def test_contact_update_rejects_invalid_url_without_losing_original(self):
        from jobpilot.core import read_json
        self.post('/networking', {'name':'Original'}, page='/networking')
        contact = read_json(self.home / 'networking.json')[0]
        result = self.post('/networking/' + contact['id'] + '/update',
            {'name':'Changed','url':'javascript:alert(1)'}, page='/networking')
        self.assertIn(b'http', result.data)
        self.assertEqual(read_json(self.home / 'networking.json')[0]['name'], 'Original')

    def setUp(self):
        super().setUp()
        self.login()

    def test_tracker_uses_actual_application_states(self):
        key = self.app.extensions['jobpilot_store'].add({'url': 'https://example.com/interview', 'company': 'Interview Company', 'title': 'Engineer'})
        self.app.extensions['jobpilot_store'].update(key, 'INTERVIEW', note='User verified invitation')
        result = self.client.get('/tracker', base_url='https://localhost')
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'Interview Company', result.data)
        self.assertIn(b'Not available', result.data)

    def test_contacts_are_private_and_no_outreach_is_sent(self):
        result = self.post('/networking', {'name': 'Recruiter Example', 'company': 'Example', 'email': 'recruiter@example.com', 'notes': 'Met at an event'}, page='/networking')
        self.assertIn(b'Recruiter Example', result.data)
        self.assertIn(b'No message has been sent', result.data)

    def test_settings_does_not_echo_provider_secrets(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {'STRIPE_SECRET_KEY': 'test-private-provider-secret'}):
            result = self.client.get('/settings', base_url='https://localhost')
        self.assertEqual(result.status_code, 200)
        self.assertNotIn(b'test-private-provider-secret', result.data)
