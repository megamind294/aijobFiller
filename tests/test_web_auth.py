from web_support import WebCase


class WebAuthTests(WebCase):
    def test_private_routes_require_login(self):
        for path in ('/', '/jobs', '/profile', '/sources', '/export', '/jobs/abc/resume'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)

    def test_login_logout_and_csrf(self):
        self.assertEqual(self.client.post('/login', data={'password': 'x'}).status_code, 400)
        response = self.post('/login', {'username': 'admin', 'password': 'wrong'}, page='/login')
        self.assertIn(b'Invalid username or password', response.data)
        self.assertIn(b'Your job search', self.login().data)
        self.assertEqual(self.client.post('/logout').status_code, 400)
        self.post('/logout')
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_password_rotation_revokes_other_sessions(self):
        other = self.app.test_client()
        self.login(other)
        self.login()
        response = self.post('/password', {
            'current_password': 'correct horse battery staple',
            'new_password': 'new secure password for jobpilot',
            'confirm_password': 'new secure password for jobpilot',
        }, page='/password')
        self.assertIn(b'Password updated', response.data)
        self.assertEqual(other.get('/').status_code, 302)

    def test_login_throttles_repeated_failures(self):
        for _ in range(5):
            self.post('/login', {'username': 'admin', 'password': 'wrong'}, page='/login')
        response = self.post('/login', {'username': 'admin', 'password': 'wrong'}, page='/login')
        self.assertEqual(response.status_code, 429)

    def test_private_responses_have_security_headers(self):
        self.login()
        response = self.client.get('/')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_login_does_not_redirect_to_external_site(self):
        response = self.post('/login?next=https://attacker.example/', {
            'username': 'admin', 'password': 'correct horse battery staple'}, page='/login')
        self.assertIn(b'Your job search', response.data)
