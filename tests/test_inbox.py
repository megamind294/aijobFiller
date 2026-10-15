import tempfile
import unittest
from pathlib import Path
from jobpilot.core import Store


class InboxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        self.a = self.store.add(dict(url='https://example.com/jobs/123', title='Python Engineer', company='Acme', location='', description='', source='manual'))
        self.b = self.store.add(dict(url='https://example.com/jobs/456', title='Data Engineer', company='Acme', location='', description='', source='manual'))

    def inbox(self):
        from jobpilot.inbox import Inbox
        return Inbox(self.store)

    def message(self, text, stamp=2000000000, id='m1'):
        return dict(id=id, thread_id='t1', subject='Application update', text=text, sender='jobs@acme.test', timestamp=stamp)

    def test_company_alone_is_review_and_role_disambiguates(self):
        box = self.inbox()
        box.ingest([self.message('Acme would like to schedule an interview')])
        self.assertIsNone(box.get('m1')['job_id'])
        box.ingest([self.message('Acme Python Engineer: interview invitation', id='m2')])
        self.assertEqual(box.get('m2')['job_id'], self.a)

    def test_exact_identifier_and_dedup(self):
        box = self.inbox()
        msg = self.message('https://example.com/jobs/123 interview invitation')
        self.assertEqual(box.ingest([msg]), 1)
        self.assertEqual(box.ingest([msg]), 0)
        self.assertEqual(box.get('m1')['job_id'], self.a)
        self.assertEqual(self.store.get(self.a)['status'], 'INTERVIEW')

    def test_old_message_and_manual_terminal_status_cannot_be_overwritten(self):
        self.store.update(self.a, 'OFFER', 'Manual decision')
        box = self.inbox()
        box.ingest([self.message('https://example.com/jobs/123 interview invitation')])
        self.assertEqual(self.store.get(self.a)['status'], 'OFFER')
        self.store.update(self.b, 'SUBMITTED')
        box.ingest([self.message('https://example.com/jobs/456 interview invitation', stamp=1, id='old')])
        self.assertEqual(self.store.get(self.b)['status'], 'SUBMITTED')

    def test_manual_relink_is_preserved_on_sync_and_draft_local(self):
        box = self.inbox()
        msg = self.message('Acme Python Engineer interview invitation')
        box.ingest([msg])
        box.route('m1', self.b)
        box.ingest([msg])
        self.assertEqual(box.get('m1')['job_id'], self.b)
        self.assertEqual(box.get('m1')['routing'], 'manual')
        box.save_draft('m1', 'Thank you. I will review the details.')
        self.assertEqual(box.get('m1')['draft'], 'Thank you. I will review the details.')

    def test_conflicting_identifiers_remain_review(self):
        box = self.inbox()
        box.ingest([self.message('https://example.com/jobs/123 https://example.com/jobs/456 interview invitation')])
        self.assertIsNone(box.get('m1')['job_id'])

    def test_message_never_proves_submission(self):
        box = self.inbox()
        box.ingest([self.message('https://example.com/jobs/123 Thanks for applying!')])
        self.assertEqual(self.store.get(self.a)['status'], 'DISCOVERED')

    def test_url_prefix_does_not_match_other_role_and_regret_is_not_rejection(self):
        box = self.inbox()
        box.ingest([self.message('https://example.com/jobs/123.other interview invitation')])
        self.assertIsNone(box.get('m1')['job_id'])
        box.ingest([self.message('https://example.com/jobs/456 Unfortunately we need to reschedule.', id='m2')])
        self.assertEqual(box.get('m2')['category'], 'REVIEW')

    def test_older_mail_cannot_overwrite_newer_mail_outcome(self):
        box = self.inbox()
        box.ingest([self.message('https://example.com/jobs/123 interview invitation', id='new')])
        box.ingest([self.message('https://example.com/jobs/123 your application was not selected', stamp=1900000000, id='old')])
        self.assertEqual(self.store.get(self.a)['status'], 'INTERVIEW')


if __name__ == '__main__':
    unittest.main()
