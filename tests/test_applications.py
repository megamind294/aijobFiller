import json
import tempfile
import unittest
from pathlib import Path
from jobpilot.core import Store, write_json
from test_workflow import PROFILE


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.applications import Applications
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.store = Store(self.home)
        self.key = self.store.add({'url': 'https://example.com/jobs/1', 'title': 'Frontend developer', 'company': 'Example', 'description': 'React TypeScript'})
        write_json(self.home / 'profile.json', PROFILE)
        self.apps = Applications(self.store)

    def test_preparation_is_idempotent_and_keeps_exact_source(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='same-click')
        again = self.apps.prepare(self.key, PROFILE, request_key='same-click')
        self.assertEqual(run['id'], again['id'])
        self.assertTrue(Path(run['resume_path']).is_file())
        self.assertEqual(self.apps.snapshot(run['id'])['profile']['personal'], PROFILE['personal'])
        self.assertEqual(run['state'], 'DOCUMENT_REVIEW')

    def test_profile_edit_invalidates_pending_approval_but_keeps_artifact(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='prepare')
        changed = json.loads(json.dumps(PROFILE))
        changed['summary'] = 'A corrected factual summary.'
        write_json(self.home / 'profile.json', changed)
        with self.assertRaises(ValueError):
            self.apps.approve_documents(run['id'], run['document_hash'])
        self.assertTrue(Path(run['resume_path']).is_file())

    def test_document_and_form_approval_are_independent(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='prepare')
        self.apps.approve_documents(run['id'], run['document_hash'])
        approved = self.apps.get(run['id'])
        self.assertEqual(approved['state'], 'READY_TO_FILL')
        self.assertFalse(approved['form_approved'])
        with self.assertRaises(ValueError):
            self.apps.record_submission(run['id'], {'clicked': True})

    def test_required_unanswered_and_stale_form_prevent_submission(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='prepare')
        self.apps.approve_documents(run['id'], run['document_hash'])
        self.apps.record_form(run['id'], {'fields': {'Name': 'Test'}, 'unanswered_required': ['Work authorization']})
        current = self.apps.get(run['id'])
        with self.assertRaises(ValueError):
            self.apps.approve_form(run['id'], current['form_hash'])

    def test_only_observed_receipt_can_mark_submitted(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='prepare')
        self.apps.approve_documents(run['id'], run['document_hash'])
        self.apps.record_form(run['id'], {'fields': {'Name': 'Test'}, 'unanswered_required': []})
        current = self.apps.get(run['id'])
        self.apps.approve_form(run['id'], current['form_hash'])
        self.apps.record_submission(run['id'], {'confirmation_text': 'Thank you for applying', 'confirmation_url': 'https://example.com/thanks', 'observed': True})
        self.assertEqual(self.apps.get(run['id'])['state'], 'SUBMITTED')
        self.assertEqual(self.store.get(self.key)['status'], 'SUBMITTED')

    def test_artifact_tampering_blocks_approval(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='tamper')
        Path(run['resume_path']).write_bytes(b'%PDF changed')
        with self.assertRaises(ValueError):
            self.apps.approve_documents(run['id'], run['document_hash'])

    def test_letter_edit_creates_new_immutable_version(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='letter')
        original = Path(run['letter_path']).read_bytes()
        edited = self.apps.edit_letter(run['id'], 'Dear team,\nMy factual letter.', 'edit-1')
        self.assertNotEqual(run['id'], edited['id'])
        self.assertEqual(Path(run['letter_path']).read_bytes(), original)
        self.assertEqual(self.apps.snapshot(edited['id'])['cover_letter'], 'Dear team,\nMy factual letter.')
        self.assertFalse(edited['document_approved'])

    def test_duplicate_job_after_submission_is_blocked(self):
        self.test_only_observed_receipt_can_mark_submitted()
        with self.assertRaises(ValueError):
            self.apps.prepare(self.key, PROFILE, request_key='duplicate')

    def test_artifact_path_rejects_escape(self):
        run = self.apps.prepare(self.key, PROFILE, request_key='path')
        with self.store.connect() as db:
            db.execute('UPDATE application_runs SET resume_path=? WHERE id=?', (str(self.home / 'profile.json'), run['id']))
        with self.assertRaises(ValueError):
            self.apps.artifact(run['id'], 'resume')

    def test_failure_cannot_erase_submitted_or_uncertain_state(self):
        self.test_only_observed_receipt_can_mark_submitted()
        run = self.apps.rows()[0]
        self.apps.fail(run['id'], 'late housekeeping failure')
        self.assertEqual(self.apps.get(run['id'])['state'], 'SUBMITTED')
