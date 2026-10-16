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

    def test_manual_handoff_records_user_evidence_without_employer_receipt(self):
        run = self.apps.prepare(self.key, PROFILE, 'manual-handoff')
        report = self.apps.record_manual_submission(run['id'], 'Portal confirmation reference ABC-123', True, run['document_hash'])
        self.assertEqual(report['state'], 'SUBMITTED')
        self.assertEqual(report['receipt'], '{}')
        evidence = json.loads(report['manual_evidence'])
        self.assertEqual(evidence['verification'], 'user-reported')
        self.assertEqual(evidence['document_hash'], run['document_hash'])
        self.assertEqual(evidence['note'], 'Portal confirmation reference ABC-123')
        self.assertEqual(self.store.get(self.key)['status'], 'SUBMITTED')
        again = self.apps.record_manual_submission(run['id'], evidence['note'], True, run['document_hash'])
        self.assertEqual(again['manual_evidence'], report['manual_evidence'])
        with self.assertRaises(ValueError):
            self.apps.record_manual_submission(run['id'], 'Different report cannot overwrite', True, run['document_hash'])
        with self.assertRaises(ValueError):
            self.apps.prepare(self.key, PROFILE, 'duplicate-after-manual')

    def test_manual_reporting_requires_attestation_evidence_and_exact_version(self):
        run = self.apps.prepare(self.key, PROFILE, 'manual-validation')
        for note, attested, document_hash in [('Portal ABC-123', False, run['document_hash']),
                                              ('', True, run['document_hash']),
                                              ('Portal ABC-123', True, 'wrong-version')]:
            with self.subTest(note=note, attested=attested, document_hash=document_hash):
                with self.assertRaises(ValueError):
                    self.apps.record_manual_submission(run['id'], note, attested, document_hash)
        self.assertEqual(self.apps.get(run['id'])['state'], 'DOCUMENT_REVIEW')

    def test_manual_reconciliation_preserves_historical_snapshot_after_source_edit(self):
        run = self.apps.prepare(self.key, PROFILE, 'uncertain-manual')
        changed = json.loads(json.dumps(PROFILE))
        changed['summary'] = 'Corrected after the earlier application.'
        write_json(self.home / 'profile.json', changed)
        with self.assertRaises(ValueError):
            self.apps.record_manual_submission(run['id'], 'Confirmed in portal ABC-123', True, run['document_hash'])
        self.apps.fail(run['id'], 'Employer receipt not observed', uncertain=True)
        report = self.apps.record_manual_submission(run['id'], 'Confirmed in portal ABC-123', True, run['document_hash'])
        self.assertEqual(report['state'], 'SUBMITTED')
        self.assertEqual(json.loads(report['manual_evidence'])['profile_hash'], run['profile_hash'])
        self.assertEqual(self.apps.snapshot(run['id'])['profile'], PROFILE)

    def test_manual_reporting_cannot_race_queued_worker_or_accept_tampered_artifacts(self):
        from jobpilot.worker import TaskQueue
        run = self.apps.prepare(self.key, PROFILE, 'queued-manual')
        queue = TaskQueue(self.store)
        queue.enqueue(run['id'], 'fill')
        with self.assertRaises(ValueError):
            self.apps.record_manual_submission(run['id'], 'Confirmed in portal ABC-123', True, run['document_hash'])
        task = queue.claim('worker')
        with self.assertRaises(ValueError):
            self.apps.record_manual_submission(run['id'], 'Confirmed in portal ABC-123', True, run['document_hash'])
        queue.complete(task['id'], 'worker', 'PAUSED')
        self.apps.fail(run['id'], 'Unconfirmed', uncertain=True)
        Path(run['resume_path']).write_bytes(b'%PDF tampered')
        with self.assertRaises(ValueError):
            self.apps.record_manual_submission(run['id'], 'Confirmed in portal ABC-123', True, run['document_hash'])

    def test_manual_reporting_cannot_overwrite_observed_employer_receipt(self):
        self.test_only_observed_receipt_can_mark_submitted()
        run = self.apps.rows()[0]
        with self.assertRaises(ValueError):
            self.apps.record_manual_submission(run['id'], 'Other reported evidence', True, run['document_hash'])
        self.assertTrue(json.loads(self.apps.get(run['id'])['receipt'])['observed'])

    def test_historical_manual_reconciliation_keeps_later_tracker_outcome(self):
        run = self.apps.prepare(self.key, PROFILE, 'historical-outcome')
        self.apps.fail(run['id'], 'Receipt was not observed', uncertain=True)
        self.store.update(self.key, 'INTERVIEW', note='Interview invitation checked', evidence='Interview reference INT-123')
        self.apps.record_manual_submission(run['id'], 'Earlier application confirmed APP-123', True, run['document_hash'])
        job = self.store.get(self.key)
        self.assertEqual(job['status'], 'INTERVIEW')
        self.assertEqual(job['evidence'], 'Interview reference INT-123')
        self.assertEqual(self.apps.get(run['id'])['state'], 'SUBMITTED')
