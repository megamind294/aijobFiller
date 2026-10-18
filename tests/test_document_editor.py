import copy
import tempfile
import unittest
from pathlib import Path
from pypdf import PdfReader
from jobpilot.profile_forms import document_preferences, parse_profile_form
from jobpilot.profile_vault import ProfileVault, render_baseline
from jobpilot.resume import render_pdf

PROFILE = {'personal': {'first_name':'Ada','last_name':'Lovelace','email':'private@example.test','phone':'12345','city':'Warsaw','state':'Mazovia','country':'Poland','github':'https://github.com/example'},'summary':'Factual summary','skills':['Python'],'experience':[{'role':'Engineer','company':'Example','start':'2020','end':'2024','bullets':['Built verified tools.']}],'projects':[],'education':[],'languages':['English'],'facts_verified':False}

def text(path):
    return '\n'.join(page.extract_text() for page in PdfReader(path).pages)

class DocumentEditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_application_pdf_obeys_section_order_and_hidden_contact_fields(self):
        profile=copy.deepcopy(PROFILE)
        profile['document_preferences']={'sections':['skills','summary'],'header_fields':['city','state'],'alignment':'justify'}
        render_pdf(profile,self.root/'application.pdf')
        result=text(self.root/'application.pdf')
        self.assertNotIn('private@example.test',result)
        self.assertNotIn('Built verified tools.',result)
        self.assertIn('Mazovia',result)
        self.assertLess(result.index('Python'),result.index('Factual summary'))
        render_baseline(profile,self.root/'baseline.pdf',profile['document_preferences'])
        self.assertEqual(result,text(self.root/'baseline.pdf'))
    def test_formatting_versions_preserve_old_pdf_and_source_facts(self):
        vault=ProfileVault(self.root);first=vault.save(copy.deepcopy(PROFILE));old=vault.pdf(first['id'],1);old_bytes=old.read_bytes()
        changed=copy.deepcopy(PROFILE);changed['document_preferences']={'sections':['summary'],'header_fields':[],'alignment':'left'}
        vault.save(changed,first['id'],expected_version=1);new=vault.pdf(first['id'],2)
        self.assertEqual(old.read_bytes(),old_bytes)
        self.assertIn('private@example.test',text(old));self.assertNotIn('private@example.test',text(new))
        self.assertEqual(vault.get(first['id'])['profile']['personal'],PROFILE['personal'])
    def test_bounded_preference_validation_and_form_round_trip(self):
        for prefs in ({'alignment':'unsafe'},{'header_fields':['email','email']},{'header_fields':['password']},{'unknown_setting':True}):
            with self.subTest(prefs=prefs),self.assertRaises(ValueError):document_preferences(prefs)
        result=parse_profile_form({'preference_alignment':'justify','preference_header_fields':'city,state','preference_sections':'skills,summary'},copy.deepcopy(PROFILE))
        self.assertEqual(result['document_preferences']['header_fields'],['city','state'])
        self.assertEqual(result['document_preferences']['alignment'],'justify')
        self.assertEqual(PROFILE['personal']['email'],'private@example.test')
    def test_hidden_fields_are_removed_from_baseline_even_when_all_sections_hidden(self):
        path=self.root/'minimal.pdf'
        render_baseline(PROFILE,path,{'sections':[],'header_fields':[]})
        result=text(path);self.assertIn('Ada',result);self.assertNotIn('12345',result)
