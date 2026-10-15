import tempfile
import unittest
from pathlib import Path
from jobpilot.core import Store, canonical_url, validate_profile, excel_safe
from jobpilot.resume import tailor
from jobpilot.gmail import matching_code
from jobpilot.browser import answer_for

class CoreTests(unittest.TestCase):
    def test_url_keeps_job_identifier(self):
        self.assertEqual(canonical_url('https://EXAMPLE.com/job?gh_jid=42&utm_source=x#apply'), 'https://example.com/job?gh_jid=42')
        self.assertNotEqual(canonical_url('https://x.com/?id=1'), canonical_url('https://x.com/?id=2'))
    def test_deduplicate_preserves_submitted(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)); j={'url':'https://example.com/job','title':'React','company':'A','description':'React'}
            key=s.add(j); s.update(key,'SUBMITTED',note='receipt')
            self.assertEqual(key,s.add(j)); self.assertEqual(s.get(key)['status'],'SUBMITTED')
            self.assertFalse(s.can_apply(key))
    def test_uncertain_submission_cannot_retry(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)); key=s.add({'url':'https://example.com/a','title':'A','company':'B'})
            s.update(key,'SUBMITTING'); self.assertFalse(s.can_apply(key))
            s.update(key,'SUBMISSION_UNCONFIRMED'); self.assertFalse(s.can_apply(key))
    def test_profile_requires_factual_confirmation(self):
        with self.assertRaises(ValueError): validate_profile({'personal':{'first_name':'R'}})
    def test_formula_escape(self):
        for value in ['=WEBSERVICE("x")','+cmd','-1','@SUM(A1)',' \t=1']:
            self.assertTrue(excel_safe(value).startswith("'"))
        self.assertEqual(excel_safe('React'), 'React')
    def test_tailoring_preserves_facts(self):
        p={'personal':{'first_name':'R'},'summary':'Developer', 'skills':['React','CSS'], 'experience':[{'company':'A','role':'Dev','start':'2022','end':'2024','bullets':['Built CSS styles','Built React components']}], 'education':[], 'projects':[]}
        t=tailor(p,{'title':'React','description':'React Kubernetes'})
        self.assertEqual(set(t['skills']),set(p['skills']))
        self.assertEqual(t['experience'][0]['bullets'][0],'Built React components')
        self.assertEqual(t['experience'][0]['end'],'2024')
        self.assertNotIn('Kubernetes',str(t))
        self.assertEqual(p['experience'][0]['bullets'][0],'Built CSS styles')
    def test_otp_scope(self):
        rule={'sender':'jobs@example.com','recipient':'me@example.com','subject_contains':'verification'}
        msg={'sender':'jobs@example.com','recipients':['me@example.com'],'subject':'Your verification','text':'Your code is 123456','timestamp':1000}
        self.assertEqual(matching_code(msg,rule,999),'123456')
        self.assertIsNone(matching_code(msg,rule,1001))
        self.assertIsNone(matching_code({**msg,'sender':'evil@example.com'},rule,999))
        self.assertIsNone(matching_code({**msg,'recipients':['other@example.com']},rule,999))
    def test_otp_ambiguous(self):
        msg={'sender':'a@b.com','recipients':['x@y.com'],'subject':'OTP','text':'123456 or 987654','timestamp':1000}
        self.assertIsNone(matching_code(msg,{'sender':'a@b.com','recipient':'x@y.com','subject_contains':'OTP'},999))
    def test_field_mapping_does_not_guess_sensitive_answers(self):
        p={'personal':{'first_name':'Rinkle','last_name':'Sharma','email':'r@example.com'},'answers':{}}
        self.assertEqual(answer_for('First Name',p),'Rinkle')
        self.assertEqual(answer_for('Email address',p),'r@example.com')
        self.assertIsNone(answer_for('Do you require sponsorship?',p))
        self.assertIsNone(answer_for('Gender',p))
        self.assertIsNone(answer_for('Password',p))

if __name__=='__main__': unittest.main()
