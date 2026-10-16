from web_support import WebCase


class WebAuthTests(WebCase):
    def test_signed_in_owner_is_not_shown_another_login_form(self):
        self.login()
        for path in ('/login', '/signin'):
            result = self.client.get(path, base_url='https://localhost')
            self.assertEqual(result.status_code, 302)
            self.assertEqual(result.headers['Location'], '/')

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

    def test_expired_session_requires_login(self):
        from datetime import timedelta
        self.login()
        self.app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=-1)
        self.assertEqual(self.client.get('/jobs').status_code, 302)

    def test_valid_login_with_origin_but_no_referrer(self):
        token = self.csrf()
        response = self.client.post('/login', base_url='https://localhost', data={
            'username': 'admin', 'password': 'correct horse battery staple', 'csrf_token': token},
            headers={'Origin': 'https://localhost'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_cross_origin_post_is_rejected_even_with_token(self):
        token = self.csrf()
        response = self.client.post('/login', base_url='https://localhost', data={
            'username': 'admin', 'password': 'correct horse battery staple', 'csrf_token': token},
            headers={'Origin': 'https://attacker.example', 'Referer': 'https://localhost/login'})
        self.assertEqual(response.status_code, 400)

    def test_missing_referrer_does_not_bypass_csrf_validation(self):
        token = self.csrf()
        response = self.client.post('/login', base_url='https://localhost', data={
            'username': 'admin', 'password': 'correct horse battery staple', 'csrf_token': token + '-invalid'},
            headers={'Origin': 'https://localhost'})
        self.assertEqual(response.status_code, 400)

    def test_privacy_browser_without_either_header_still_requires_valid_token(self):
        token = self.csrf()
        response = self.client.post('/login', base_url='https://localhost', data={
            'username': 'admin', 'password': 'correct horse battery staple', 'csrf_token': token})
        self.assertEqual(response.status_code, 302)

    def test_mismatched_referrer_without_origin_is_rejected(self):
        token = self.csrf()
        response = self.client.post('/login', base_url='https://localhost', data={
            'username': 'admin', 'password': 'correct horse battery staple', 'csrf_token': token},
            headers={'Referer': 'https://attacker.example/page'})
        self.assertEqual(response.status_code, 400)

    def test_favicon_request_does_not_invalidate_login_form(self):
        token = self.csrf()
        self.client.get('/favicon.ico', base_url='https://localhost', follow_redirects=True)
        response = self.client.post('/login', base_url='https://localhost', data={
            'username': 'admin', 'password': 'correct horse battery staple', 'csrf_token': token},
            headers={'Referer': 'https://localhost/login'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/')

    def test_anonymous_redirect_preserves_open_login_form(self):
        token = self.csrf()
        self.client.get('/jobs', base_url='https://localhost', follow_redirects=True)
        response = self.client.post('/login', base_url='https://localhost', data={
            'username': 'admin', 'password': 'correct horse battery staple', 'csrf_token': token},
            headers={'Referer': 'https://localhost/login'})
        self.assertEqual(response.status_code, 302)
