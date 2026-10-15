import tempfile
import unittest
from pathlib import Path
from jobpilot.core import Store
from jobpilot.browser import profile_value,allowed_frame,adapter_scope

class GuardsTests(unittest.TestCase):
    def test_work_history_mapping_supports_lists(self):
        self.assertEqual(profile_value({'experience':[{'company':'Real Employer'}]},'experience.0.company'),'Real Employer')
    def test_no_personal_data_filled_in_untrusted_iframe(self):
        self.assertFalse(allowed_frame('https://ads.example/ad','https://jobs.example/apply',[]))
        self.assertTrue(allowed_frame('https://jobs.example/form','https://jobs.example/apply',[]))
        self.assertTrue(allowed_frame('https://forms.example/form','https://jobs.example/apply',['https://forms.example']))
        self.assertFalse(allowed_frame('about:blank','https://jobs.example/apply',[]))
    def test_claim_is_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d); key=s.add({'url':'https://example.com','company':'A','title':'React'})
            s.claim(key)
            with self.assertRaises(ValueError): Store(d).claim(key)
    def test_adapter_rejects_different_origin(self):
        class Page: url='https://evil.example/form'
        with self.assertRaises(ValueError): adapter_scope(Page(),{'origin':'https://jobs.example'})
