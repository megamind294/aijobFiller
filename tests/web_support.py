import gc
import re
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import generate_password_hash


class WebCase(unittest.TestCase):
    def setUp(self):
        from jobpilot.web import create_app
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.home = Path(self.temp.name)
        self.app = create_app({
            'TESTING': True, 'JOBPILOT_HOME': str(self.home),
            'SECRET_KEY': 'test-session-secret',
            # Test credentials avoid paying production hash costs on every fixture login.
            'PASSWORD_HASH': generate_password_hash(
                'correct horse battery staple', method='pbkdf2:sha256:1000'),
            'AUTH_VERSION': 'initial',
        })
        self.client = self.app.test_client()

    def csrf(self, client=None, path='/login'):
        response = (client or self.client).get(path, base_url='https://localhost')
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(token, response.get_data(as_text=True))
        return token.group(1)

    def post(self, path, data=None, client=None, page='/'):
        client = client or self.client
        payload = dict(data or {})
        payload['csrf_token'] = self.csrf(client, page)
        return client.post(path, data=payload, base_url='https://localhost',
                           headers={'Referer': 'https://localhost' + page}, follow_redirects=True)

    def login(self, client=None):
        return self.post('/login', {'username': 'admin', 'password': 'correct horse battery staple'}, client, '/login')
