import tempfile
import unittest
from pathlib import Path

class ProfileFormTests(unittest.TestCase):
    def test_repeatable_draft_and_answers(self):
        from jobpilot.profile_forms import parse_profile_form, profile_errors
        p = parse_profile_form({'personal_first_name':'Ada', 'experience_0_company':'One', 'experience_0_role':'Dev', 'experience_0_bullets':'Built things\nTested things', 'experience_2_company':'Two', 'answers_0_question':'Sponsorship', 'answers_0_answer':'Unknown', 'skills':'Python\nSQL'}, {})
        self.assertEqual(len(p['experience']), 2)
        self.assertEqual(p['experience'][0]['bullets'], ['Built things','Tested things'])
        self.assertEqual(p['answers'], {'Sponsorship':'Unknown'})
        self.assertFalse(p['facts_verified'])
        self.assertEqual(profile_errors(p), {})
        p['facts_verified'] = True
        self.assertIn('personal_email', profile_errors(p))
    def test_invalid_preference_and_duplicate_answer(self):
        from jobpilot.profile_forms import parse_profile_form
        with self.assertRaises(ValueError):
            parse_profile_form({'answers_0_question':'Q','answers_0_answer':'a','answers_1_question':'Q','answers_1_answer':'b'}, {})
        with self.assertRaises(ValueError):
            parse_profile_form({'preference_template':'javascript'}, {})

from web_support import WebCase
from jobpilot.core import read_json

class ProfileEditorWebTests(WebCase):
    def setUp(self):
        super().setUp()
        from jobpilot.profile_vault import register_profile_vault
        if 'profiles' not in self.app.view_functions: register_profile_vault(self.app)
        self.login()
    def test_normal_draft_roundtrip_and_field_errors(self):
        data = {'personal_first_name':'Ada','projects_0_name':'Engine','projects_0_bullets':'Built engine','answers_0_question':'Visa','answers_0_answer':'Unknown','save_action':'draft'}
        r = self.post('/profile',data,page='/profile')
        self.assertIn(b'Engine',r.data)
        self.assertFalse(read_json(self.home/'profile.json')['facts_verified'])
        data.update(facts_verified='yes',save_action='save')
        r = self.post('/profile',data,page='/profile')
        self.assertIn(b'Required to verify',r.data)
        self.assertIn(b'Engine',r.data)
        self.assertFalse(read_json(self.home/'profile.json')['facts_verified'])
    def test_stale_editor_retains_exact_old_revision_and_text_preview(self):
        from jobpilot.profile_vault import ProfileVault
        v = ProfileVault(self.home)
        item = v.save({'personal':{'first_name':'Original'},'facts_verified':False},name='Primary')
        v.save({'personal':{'first_name':'New'},'facts_verified':False},item['id'],expected_version=1)
        r = self.post('/profile',{'profile_id':item['id'],'profile_version':'1','personal_first_name':'Stale'},page='/profile')
        self.assertIn(b'another tab',r.data)
        self.assertEqual(v.default()['profile']['personal']['first_name'],'New')
        r = self.client.get(f'/profiles/{item["id"]}/versions/1/preview',base_url='https://localhost')
        self.assertIn(b'Original',r.data)
        self.assertNotIn(b'>New<',r.data)
        self.assertEqual(self.client.get('/profiles/not-mine/versions/1/resume.pdf',base_url='https://localhost').status_code,404)

    def test_layout_controls_save_version_and_preview_without_changing_contact_values(self):
        response = self.post('/profile', {'personal_first_name':'Ada','personal_email':'ada@example.test',
            'preference_sections':'skills,summary','preference_header_fields':'city,state',
            'preference_alignment':'justify','save_action':'preview'}, page='/profile')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Open exact PDF',response.data)
        self.assertNotIn(b'ada@example.test',response.data)
        saved=read_json(self.home/'profile.json')
        self.assertEqual(saved['personal']['email'],'ada@example.test')
        self.assertEqual(saved['document_preferences']['sections'],['skills','summary'])
        self.assertEqual(saved['document_preferences']['header_fields'],['city','state'])
        self.assertEqual(saved['document_preferences']['alignment'],'justify')
        edit=self.client.get('/profile',base_url='https://localhost')
        self.assertEqual(edit.status_code,200)
        self.assertIn(b'data-document-sections',edit.data)
        self.assertIn(b'data-header-field="email"',edit.data)
