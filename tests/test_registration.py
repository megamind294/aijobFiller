from web_support import WebCase


class RegistrationTests(WebCase):
    def register(self, email='new@example.test', password='a secure customer password'):
        return self.post('/register', {'email':email, 'password':password,
                       'confirm_password':password, 'role':'owner'}, page='/register')

    def test_public_registration_creates_customer_with_ten_free_applications(self):
        page = self.client.get('/register', base_url='https://localhost')
        self.assertEqual(page.status_code, 200)
        response = self.register()
        self.assertIn(b'Your job search', response.data)
        with self.client.session_transaction() as session:
            account = self.app.extensions['jobpilot_accounts'].get(session['account_id'])
        self.assertEqual(account['role'], 'customer')
        self.assertEqual(account['email'], 'new@example.test')
        from jobpilot.billing import CreditLedger
        self.assertEqual(CreditLedger(self.home).balance(account['id'])['available'], 10)

    def test_customer_can_log_out_and_log_back_in_with_password(self):
        self.register()
        self.post('/logout', page='/billing')
        response = self.post('/signin', {'email':'new@example.test','password':'a secure customer password'}, page='/signin')
        self.assertIn(b'Your job search', response.data)

    def test_weak_password_and_duplicate_email_are_rejected(self):
        self.assertIn(b'14', self.register(password='short').data)
        self.register()
        self.assertIn(b'already registered', self.register(email='NEW@example.test').data)

    def test_password_is_hashed_and_registration_requires_csrf(self):
        self.register()
        self.assertNotIn(b'a secure customer password', (self.home / 'accounts.sqlite3').read_bytes())
        self.assertEqual(self.client.post('/register', base_url='https://localhost', data={}).status_code, 400)

    def test_customer_password_does_not_authenticate_owner_login(self):
        self.register()
        self.post('/logout', page='/billing')
        result = self.post('/login', {'username':'new@example.test','password':'a secure customer password'}, page='/login')
        self.assertIn(b'Invalid username or password', result.data)

    def test_password_login_is_rate_limited(self):
        for _ in range(10):
            self.post('/signin', {'email':'no@example.test','password':'wrong'}, page='/signin')
        result = self.post('/signin', {'email':'no@example.test','password':'wrong'}, page='/signin')
        self.assertEqual(result.status_code, 429)
