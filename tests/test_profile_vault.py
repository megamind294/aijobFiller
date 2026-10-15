import tempfile
import unittest
from pathlib import Path

class ProfileVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        from jobpilot.profile_vault import ProfileVault
        self.v = ProfileVault(Path(self.temp.name))
    def test_revisions_defaults_and_stale_writes(self):
        a = self.v.save({'personal':{'first_name':'Ada'}, 'facts_verified':False}, name='Primary')
        b = self.v.save({'personal':{'first_name':'Grace'}, 'facts_verified':False}, name='Other')
        self.assertEqual(self.v.default()['id'], a['id'])
        self.v.save({'personal':{'first_name':'New'}, 'facts_verified':False}, a['id'], expected_version=1)
        self.assertEqual(self.v.revision(a['id'],1)['profile']['personal']['first_name'],'Ada')
        with self.assertRaises(ValueError): self.v.save({}, a['id'], expected_version=1)
        with self.assertRaises(ValueError): self.v.delete(a['id'])
        self.v.select(b['id']); self.v.rename(b['id'],'Renamed')
        self.assertEqual(self.v.default()['name'],'Renamed')
        self.v.delete(a['id'])
        self.assertEqual(self.v.revision(a['id'],1)['profile']['personal']['first_name'],'Ada')
    def test_copy_and_scoped_storage(self):
        a = self.v.save({'personal':{},'facts_verified':False}, name='Primary')
        b = self.v.copy(a['id'],'Copy')
        self.assertNotEqual(a['id'],b['id'])
        from jobpilot.profile_vault import ProfileVault
        with tempfile.TemporaryDirectory() as other: self.assertEqual(ProfileVault(Path(other)).list(),[])
    def test_selectable_pdf_and_preferences_validation(self):
        from jobpilot.profile_vault import render_baseline
        from pypdf import PdfReader
        p = {'personal':{'first_name':'Ada','last_name':'Lovelace'},'summary':'Original factual summary','skills':['Python'],'experience':[],'projects':[],'education':[]}
        path = Path(self.temp.name)/'baseline.pdf'
        render_baseline(p,path,{'template':'classic','font':'Helvetica','size':'10','spacing':'normal','sections':['summary','skills']})
        self.assertIn('Original factual summary',''.join(x.extract_text() for x in PdfReader(path).pages))
        with self.assertRaises(ValueError): render_baseline(p,path,{'font':'../bad'})
    def test_external_import_is_preserved_as_new_revision(self):
        from jobpilot.core import write_json
        a = self.v.save({'personal':{'first_name':'Before'},'facts_verified':False})
        write_json(self.v.home/'profile.json',{'personal':{'first_name':'Imported'},'facts_verified':False})
        self.v.ensure_legacy()
        self.assertEqual(self.v.default()['version'],2)
        self.assertEqual(self.v.revision(a['id'],1)['profile']['personal']['first_name'],'Before')
    def test_unicode_source_text_survives_pdf_extraction(self):
        from jobpilot.profile_vault import render_baseline
        from pypdf import PdfReader
        path = Path(self.temp.name)/'unicode.pdf'
        render_baseline({'personal':{'first_name':'Łukasz','last_name':'Żółć'},'skills':['Inżynieria']},path)
        text = ''.join(x.extract_text() for x in PdfReader(path).pages)
        self.assertIn('Łukasz',text)
        self.assertIn('Inżynieria',text)
