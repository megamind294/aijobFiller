import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from jobpilot.core import Store, read_json, write_json
from jobpilot.applications import Applications
from test_workflow import PROFILE


def valid_rewrites():
    return {'summary': 'A frontend developer.', 'experience': [
        {'index': 0, 'bullets': [{'index': 0, 'text': 'Developed React forms.'}]}], 'projects': []}


class RewriteValidationTests(unittest.TestCase):
    def test_constrained_rewrites_preserve_protected_facts_and_provenance(self):
        from jobpilot.ai_preparation import validate_rewrites
        original = copy.deepcopy(PROFILE)
        prepared, provenance = validate_rewrites(PROFILE, json.dumps(valid_rewrites()))
        self.assertEqual(prepared['summary'], 'A frontend developer.')
        self.assertEqual(prepared['experience'][0]['bullets'], ['Developed React forms.'])
        self.assertEqual(prepared['personal'], PROFILE['personal'])
        self.assertEqual(prepared['skills'], PROFILE['skills'])
        self.assertEqual(prepared['answers'], PROFILE['answers'])
        self.assertEqual(prepared['experience'][0]['company'], 'Demo Employer')
        self.assertEqual(prepared['experience'][0]['start'], '2022')
        self.assertEqual(PROFILE, original)
        self.assertIn({'path': 'experience.0.bullets.0', 'source': 'Built React forms.',
                       'generated': 'Developed React forms.'}, provenance)

    def test_rejects_new_claims_history_and_malformed_responses(self):
        from jobpilot.ai_preparation import validate_rewrites
        fixtures = []
        for text in ['Led a global team.', 'Developed React forms for 20 clients.', 'Built Python forms.']:
            response = valid_rewrites()
            response['experience'][0]['bullets'][0]['text'] = text
            fixtures.append(json.dumps(response))
        response = valid_rewrites()
        response['experience'][0]['index'] = 1
        fixtures.append(json.dumps(response))
        response = valid_rewrites()
        response['experience'][0]['bullets'].append({'index': 1, 'text': 'Built React forms.'})
        fixtures.append(json.dumps(response))
        response = valid_rewrites()
        response['skills'] = ['Python']
        fixtures.append(json.dumps(response))
        fixtures.extend(['```json\n' + json.dumps(valid_rewrites()) + '\n```',
                         json.dumps(valid_rewrites())[:-2], '{"summary":"x","summary":"y"}',
                         json.dumps({'summary': 'A frontend developer.', 'experience': [], 'projects': []})])
        for response in fixtures:
            with self.subTest(response=response):
                with self.assertRaisesRegex(ValueError, 'factual preparation'):
                    validate_rewrites(PROFILE, response)

    def test_numeric_scope_and_negation_claims_cannot_change(self):
        from jobpilot.ai_preparation import validate_rewrites
        profile = copy.deepcopy(PROFILE)
        profile['experience'][0]['bullets'] = ['Assisted with 3 React forms without managing a team.']
        for text in ['Built 3 React forms.', 'Assisted with 30 React forms without managing a team.',
                     'Assisted with React forms without managing a team.',
                     'Assisted with 3 React forms managing a team.']:
            response = valid_rewrites()
            response['experience'][0]['bullets'][0]['text'] = text
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    validate_rewrites(profile, json.dumps(response))

    def test_rejects_strengthening_existing_words_by_removing_qualification(self):
        from jobpilot.ai_preparation import validate_rewrites
        for source, generated in [('Familiar with React forms.', 'React forms.'),
                                  ('Assisted with React forms.', 'React forms.'),
                                  ('Did not build React forms.', 'Did build React forms.'),
                                  ("Didn't build React forms.", 'Build React forms.')]:
            profile = copy.deepcopy(PROFILE)
            profile['experience'][0]['bullets'] = [source]
            response = valid_rewrites()
            response['experience'][0]['bullets'][0]['text'] = generated
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    validate_rewrites(profile, json.dumps(response))


class AIPackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        write_json(self.root / 'profile.json', PROFILE)
        self.job = self.store.add({'url': 'https://example.com/job', 'title': 'React developer',
                                   'company': 'Example', 'description': 'React forms and Python'})
        self.apps = Applications(self.store)
        self.app = Flask(__name__)
        self.app.config.update(JOBPILOT_HOME=str(self.root), GEMINI_API_KEY='test-fixture')

    def test_ai_package_is_opt_in_idempotent_reviewed_and_keeps_source_file(self):
        received = []
        def reply(messages, context, **kwargs):
            received.append(context)
            return json.dumps(valid_rewrites())
        with self.app.test_request_context(), patch('jobpilot.assistant.provider_reply', side_effect=reply):
            g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
            run = self.apps.prepare(self.job, PROFILE, 'ai-request', optimization='ai', ai_consent=True)
            again = self.apps.prepare(self.job, PROFILE, 'ai-request', optimization='ai', ai_consent=True)
            edited = self.apps.edit_letter(run['id'], 'Dear team, my factual letter.', 'ai-letter-edit')
        self.assertEqual(run['id'], again['id'])
        self.assertEqual(len(received), 1)
        self.assertNotIn('personal', received[0]['resume'])
        self.assertNotIn('answers', received[0]['resume'])
        self.assertEqual(read_json(self.root / 'profile.json'), PROFILE)
        self.assertEqual(run['state'], 'DOCUMENT_REVIEW')
        self.assertFalse(run['document_approved'])
        self.assertFalse(run['form_approved'])
        source = self.apps.snapshot(run['id'])
        self.assertEqual(source['profile'], PROFILE)
        self.assertEqual(source['prepared_profile']['summary'], 'A frontend developer.')
        self.assertEqual(source['ai_provider'], 'Google Gemini')
        self.assertTrue(source['ai_provenance'])
        self.assertEqual(self.apps.snapshot(edited['id'])['prepared_profile'], source['prepared_profile'])
        self.assertTrue(Path(run['resume_path']).read_bytes().startswith(b'%PDF'))

    def test_unconsented_invalid_and_unavailable_generation_create_no_package(self):
        from jobpilot.assistant import AssistantUnavailable
        with self.app.test_request_context():
            g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
            with self.assertRaises(ValueError):
                self.apps.prepare(self.job, PROFILE, 'no-consent', optimization='ai')
            with patch('jobpilot.assistant.provider_reply', return_value='{"truncated":'):
                with self.assertRaises(ValueError):
                    self.apps.prepare(self.job, PROFILE, 'invalid-ai', optimization='ai', ai_consent=True)
            with patch('jobpilot.assistant.provider_reply', side_effect=AssistantUnavailable('Provider unavailable')):
                with self.assertRaises(ValueError):
                    self.apps.prepare(self.job, PROFILE, 'provider-down', optimization='ai', ai_consent=True)
        self.assertEqual(self.apps.rows(), [])
        self.assertFalse((self.root / 'documents').exists())

    def test_auto_lane_cannot_choose_ai_preparation(self):
        from jobpilot.automation import Automation
        from jobpilot.billing import CreditLedger
        service = Automation(self.store, CreditLedger(self.root), 'owner', owner=True)
        with self.assertRaises(ValueError):
            service.save_lane({'name': 'AI lane', 'optimization': 'ai'})

    def test_shared_hourly_cap_blocks_generation_before_provider_or_documents(self):
        from jobpilot.assistant import claim_ai_request
        with self.app.test_request_context(), patch('jobpilot.assistant.provider_reply', side_effect=AssertionError('Provider must not be contacted after the cap')):
            g.jobpilot_account = {'id': 'owner', 'role': 'owner'}
            for _ in range(20):
                claim_ai_request()
            with self.assertRaisesRegex(ValueError, 'Hourly AI limit'):
                self.apps.prepare(self.job, PROFILE, 'capped-ai', optimization='ai', ai_consent=True)
        self.assertEqual(self.apps.rows(), [])
