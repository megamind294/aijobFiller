import copy
import tempfile
import threading
import unittest
from unittest.mock import patch
from jobpilot.core import Store,validate_profile
from jobpilot import browser
from jobpilot.demo import make_server
from jobpilot.resume import prepare

PROFILE={'facts_verified':True,'personal':{'first_name':'Demo','last_name':'Candidate','email':'demo@example.com','phone':'+48123456789','city':'Krakow','country':'Poland'},'summary':'Frontend developer.','skills':['React'],'experience':[{'company':'Demo Employer','role':'Developer','start':'2022','end':'2024','bullets':['Built React forms.']}],'education':[], 'projects':[], 'answers':{}}

class ReviewFixTests(unittest.TestCase):
    def test_invalid_profile_shapes_are_rejected(self):
        bad=[]
        p=copy.deepcopy(PROFILE); p['personal']['phone']=None; bad.append(p)
        p=copy.deepcopy(PROFILE); p['skills']='React'; bad.append(p)
        p=copy.deepcopy(PROFILE); p['experience']=[]; p['projects']=[{}]; bad.append(p)
        p=copy.deepcopy(PROFILE); p['experience'][0]['bullets']='Built forms'; bad.append(p)
        for p in bad:
            with self.subTest(profile=p),self.assertRaises(ValueError): validate_profile(p)
    def test_reimport_corrects_description_without_resetting_progress(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d); j={'url':'https://example.com/job','title':'React','company':'A'}
            key=s.add(j); s.update(key,'NEEDS_REVIEW',note='User note',resume='original.pdf')
            s.add({**j,'description':'Full React description'})
            updated=s.get(key)
            self.assertEqual(updated['description'],'Full React description')
            self.assertEqual(updated['status'],'NEEDS_REVIEW')
            self.assertEqual(updated['resume'],'original.pdf')
            self.assertEqual(updated['note'],'User note')
    def test_otp_rechecks_origin_after_mail_wait(self):
        class Page:
            url='https://jobs.example/verify'
            def locator(self,selector): raise AssertionError('Must not fill at changed origin')
        page=Page()
        class Mail:
            def otp(self,rule,since): page.url='https://other.example'; return '123456'
        with self.assertRaises(ValueError):
            browser.fill_otp(page,{'origin':'https://jobs.example','otp':{'selector':'input'}},Mail(),1000)
    def test_active_review_keeps_exclusive_claim(self):
        from playwright.sync_api import sync_playwright
        server=make_server(0); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as d, sync_playwright() as p:
                url=f'http://127.0.0.1:{server.server_port}'
                s=Store(d); key=s.add({'url':url,'title':'Demo','company':'Demo','description':'React'})
                prepare(s,key,PROFILE)
                actual_browser=p.chromium.launch(headless=True)
                context=actual_browser.new_context()
                class Chromium:
                    def launch_persistent_context(self,*args,**kwargs): return context
                class PW:
                    chromium=Chromium()
                    def __enter__(self): return self
                    def __exit__(self,*args): pass
                count=0
                def answer(prompt):
                    nonlocal count
                    count+=1
                    if count==1: return 'fill'
                    if count==2:
                        self.assertFalse(s.can_apply(key),'Active review must still hold the claim')
                        return 'pause'
                    return ''
                with patch('playwright.sync_api.sync_playwright',return_value=PW()),patch('builtins.input',side_effect=answer):
                    browser.run_application(s,key,PROFILE)
                self.assertEqual(s.get(key)['status'],'NEEDS_REVIEW')
                actual_browser.close()
        finally: server.shutdown(); server.server_close(); thread.join()
