from web_support import WebCase


class WebAccountTests(WebCase):
    def test_signin_is_public_and_unconfigured_provider_is_honest(self):
        response = self.client.get('/signin', base_url='https://localhost')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Google', response.data)
        response = self.client.get('/signin/google', base_url='https://localhost')
        self.assertIn(response.status_code, (302, 503))

    def test_customer_cannot_read_owner_jobs_or_change_owner_password(self):
        store = self.app.extensions['jobpilot_store']
        key = store.add({'url': 'https://example.com/private-role', 'title': 'Owner private role', 'company': 'Private'})
        account = self.app.extensions['jobpilot_accounts'].identity('email', 'customer@example.com', 'customer@example.com', verified=True)
        with self.client.session_transaction() as session:
            session['account_id'] = account['id']
            session['auth_version'] = account['version']
        response = self.client.get('/jobs', base_url='https://localhost')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Owner private role', response.data)
        self.assertEqual(self.client.get('/jobs/' + key, base_url='https://localhost').status_code, 404)
        self.assertEqual(self.client.get('/password', base_url='https://localhost').status_code, 403)

    def test_google_callback_rejects_missing_state(self):
        response = self.client.get('/signin/google/callback?code=not-real', base_url='https://localhost')
        self.assertEqual(response.status_code, 400)
