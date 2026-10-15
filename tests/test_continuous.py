import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from jobpilot.core import Store,write_json,read_json
from jobpilot.service import ProcessLock,Worker,backoff_seconds
from jobpilot.browser import captcha_required

class ContinuousTests(unittest.TestCase):
    def test_single_process_lock_released(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'worker.lock'
            with ProcessLock(path):
                with self.assertRaises(RuntimeError):
                    with ProcessLock(path): pass
            with ProcessLock(path): pass
    def test_worker_imports_new_jobs_and_never_submits(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d); inbox=Path(d)/'incoming'; inbox.mkdir()
            write_json(inbox/'jobs.json',[{'url':'https://example.com/job','title':'React','company':'A','description':'React'}])
            write_json(Path(d)/'sources.json',{'sources':[]})
            w=Worker(s)
            with patch('jobpilot.service.discover',side_effect=AssertionError('No configured source')): result=w.cycle()
            self.assertEqual(result['imported'],1)
            self.assertEqual(s.rows()[0]['status'],'DISCOVERED')
            self.assertFalse((inbox/'jobs.json').exists())
            self.assertEqual(w.cycle()['imported'],0)
            self.assertEqual(read_json(Path(d)/'heartbeat.json')['phase'],'idle')
    def test_bad_input_does_not_stop_good_imports(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d); inbox=Path(d)/'incoming'; inbox.mkdir()
            (inbox/'bad.json').write_text('not json')
            write_json(inbox/'good.json',[{'url':'https://example.com/job','title':'React','company':'A'}])
            write_json(Path(d)/'sources.json',{'sources':[]})
            result=Worker(s).cycle()
            self.assertEqual(len(s.rows()),1); self.assertEqual(len(result['errors']),1)
            self.assertEqual(len(list((Path(d)/'failed-imports').glob('*.json'))),1)
    def test_retry_is_capped(self):
        self.assertEqual(backoff_seconds(1),60)
        self.assertEqual(backoff_seconds(100),900)
    def test_captcha_detection_observes_existing_token_only(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True); page=browser.new_page()
            page.set_content('<iframe title="reCAPTCHA"></iframe>')
            self.assertTrue(captcha_required(page))
            page.set_content('<textarea name="g-recaptcha-response" style="display:none">user-solved-token</textarea><iframe title="reCAPTCHA"></iframe>')
            self.assertFalse(captcha_required(page))
            page.set_content('<p>Regular application form</p>')
            self.assertFalse(captcha_required(page)); browser.close()

    def test_prepare_cannot_change_resume_during_browser_session(self):
        from jobpilot.resume import prepare
        with tempfile.TemporaryDirectory() as d:
            s=Store(d)
            key=s.add({'url':'https://example.com/job','title':'React','company':'A','description':'React'})
            with ProcessLock(Path(d)/'browser.lock'):
                with self.assertRaises(RuntimeError): prepare(s,key,{})
            self.assertEqual(s.get(key)['resume'],'')

    def test_stop_request_exits_worker(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d); w=Worker(s)
            def cycle():
                (Path(d)/'worker.stop').touch()
                return {'errors':[]}
            with patch.object(w,'cycle',side_effect=cycle): w.run(interval=60)
            self.assertEqual(read_json(Path(d)/'heartbeat.json')['phase'],'stopped')
