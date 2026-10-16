import json
from unittest.mock import patch
from tests.web_support import WebCase


class AssistantTests(WebCase):
    def request_message(self, message='Help with my next step'):
        return self.client.post('/assistant/message', base_url='https://localhost',
            json={'message':message}, headers={'X-CSRFToken':self.csrf(path='/'), 'Origin':'https://localhost'})

    def test_assistant_requires_login_and_csrf(self):
        self.assertEqual(self.client.get('/assistant/history', base_url='https://localhost').status_code, 302)
        self.login()
        self.assertEqual(self.client.post('/assistant/message', base_url='https://localhost', json={'message':'Hi'}).status_code, 400)

    def test_reply_uses_scoped_context_and_persists_messages_without_secrets(self):
        self.login()
        self.app.config['GEMINI_API_KEY'] = 'sk-private-fixture'
        self.app.extensions['jobpilot_store'].add({'url':'https://example.com/role','title':'React Engineer','company':'Example'})
        with patch('jobpilot.assistant.provider_reply', return_value='Review your profile, then browse jobs.') as reply:
            result = self.request_message()
        self.assertEqual(result.status_code, 200)
        self.assertIn('React Engineer', json.dumps(reply.call_args.args[1]))
        history = self.client.get('/assistant/history', base_url='https://localhost').get_json()
        self.assertEqual(len(history['messages']), 2)
        self.assertNotIn('sk-private-fixture', json.dumps(history))
        self.assertEqual(self.app.extensions['jobpilot_store'].rows()[0]['status'], 'DISCOVERED')

    def test_unconfigured_and_invalid_messages_do_not_call_provider(self):
        self.login()
        self.app.config['GEMINI_API_KEY'] = ''
        with patch('jobpilot.assistant.provider_reply') as reply:
            self.assertEqual(self.request_message().status_code, 503)
            self.assertEqual(self.request_message(' ').status_code, 400)
            reply.assert_not_called()

    def test_customer_cannot_read_owner_conversation_or_context(self):
        self.login()
        self.app.config['GEMINI_API_KEY'] = 'sk-private-fixture'
        self.app.extensions['jobpilot_store'].add({'url':'https://example.com/private','title':'Owner private role','company':'Private'})
        with patch('jobpilot.assistant.provider_reply', return_value='Owner private answer'):
            self.request_message('Owner private question')
        account = self.app.extensions['jobpilot_accounts'].identity('email','customer@example.test',verified=True)
        from jobpilot.billing import CreditLedger
        CreditLedger(self.home).grant(account['id'], 'paid-fixture', 1, 'wallet')
        with self.client.session_transaction() as session:
            session.update(account_id=account['id'], auth_version=account['version'])
        self.assertEqual(self.client.get('/assistant/history', base_url='https://localhost').get_json()['messages'], [])
        with patch('jobpilot.assistant.provider_reply', return_value='Customer reply') as reply:
            self.assertEqual(self.request_message().status_code, 200)
        self.assertNotIn('Owner private', json.dumps(reply.call_args.args))

    def test_hourly_limit_rejects_before_provider_call(self):
        self.login()
        self.app.config['GEMINI_API_KEY'] = 'sk-private-fixture'
        import sqlite3, time
        self.client.get('/assistant/history', base_url='https://localhost')
        with sqlite3.connect(self.home / 'assistant.sqlite3') as db:
            db.executemany('INSERT INTO requests VALUES(?)', [(time.time(),)]*20)
        with patch('jobpilot.assistant.provider_reply') as reply:
            self.assertEqual(self.request_message().status_code, 429)
        reply.assert_not_called()

    def test_provider_output_collects_text_messages_and_disables_storage(self):
        from jobpilot.assistant import provider_reply
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'status':'completed','steps':[
            {'type':'reasoning'}, {'type':'model_output','content':[{'type':'text','text':'Ready.'}]}]}).encode()
        with self.app.app_context(), patch('jobpilot.assistant.urlopen', return_value=response) as send:
            self.app.config['GEMINI_API_KEY']='sk-private-fixture'
            self.assertEqual(provider_reply([], {}), 'Ready.')
        payload = json.loads(send.call_args.args[0].data)
        self.assertFalse(payload['store'])
        self.assertLessEqual(payload['generation_config']['max_output_tokens'], 800)
        self.assertNotIn('tools', payload)
