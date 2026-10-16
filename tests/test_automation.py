import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from jobpilot.core import Store, write_json
from jobpilot.billing import CreditLedger
from test_workflow import PROFILE


class AutomationTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.automation import Automation
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.ledger = CreditLedger(self.root)
        self.ledger.grant('user', 'paid-fixture', 5, 'wallet')
        write_json(self.root / 'profile.json', PROFILE)
        self.automation = Automation(self.store, self.ledger, 'user', entitled=True)
        self.job = self.store.add({'url': 'https://example.com/job', 'title': 'React engineer', 'company': 'Example', 'description': 'React TypeScript', 'location': 'Poland'})

    def test_default_off_and_activation_required(self):
        lane = self.automation.save_lane({'name': 'React', 'keywords': ['React']})
        self.assertFalse(lane['active'])
        self.assertEqual(self.automation.run(), [])
        self.automation.activate(lane['id'], True)
        self.assertEqual(len(self.automation.run()), 1)
        self.assertEqual(len(self.automation.run()), 0)

    def test_application_retains_original_mode_when_lane_changes(self):
        lane = self.automation.save_lane({'name': 'Automatic', 'mode': 'automatic'})
        self.automation.adapters = {'https://example.com': {'origin': 'https://example.com'}}
        self.automation.activate(lane['id'], True)
        identifier = self.automation.run()[0]
        self.automation.save_lane({'name': 'Review now', 'mode': 'review-first'}, lane['id'])
        with self.store.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(automation_applications)')}
            self.assertIn('submission_mode', columns)
            origin = db.execute('SELECT submission_mode FROM automation_applications WHERE application_id=?', (identifier,)).fetchone()
        self.assertEqual(origin['submission_mode'], 'automatic')
        self.assertFalse(self.automation.authorized_automatic(identifier))

    def test_legacy_lane_packages_require_automatic_authorization(self):
        from jobpilot.automation import Automation
        with self.store.connect() as db:
            db.execute('DROP TABLE automation_applications')
            db.execute('CREATE TABLE automation_applications (application_id TEXT PRIMARY KEY,lane_id TEXT,config_hash TEXT)')
            db.execute("INSERT INTO automation_applications VALUES('legacy-app','legacy-lane','old-hash')")
        Automation(self.store, self.ledger, 'user', entitled=True)
        with self.store.connect() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(automation_applications)')}
            self.assertIn('submission_mode', columns)
            origin = db.execute("SELECT submission_mode FROM automation_applications WHERE application_id='legacy-app'").fetchone()
        self.assertEqual(origin['submission_mode'], 'automatic')

    def test_cap_failure_reuses_current_unqueued_package_after_capacity_returns(self):
        from jobpilot.applications import Applications
        self.automation.settings(daily_cap=1)
        lane = self.automation.save_lane({'name': 'Review', 'mode': 'review-first'})
        self.automation.activate(lane['id'], True)
        preview = self.automation.preview()
        self.ledger.reserve('user', 'occupied-slot', preview['day'], 1)
        self.assertEqual(self.automation.run(), [])
        apps = Applications(self.store)
        self.assertEqual(len(apps.rows()), 1)
        paused_id = apps.rows()[0]['id']
        self.ledger.settle('user', 'occupied-slot', False)
        self.assertTrue(self.automation.preview()['candidates'][0]['eligible'])
        self.assertEqual(self.automation.run(), [paused_id])
        self.assertEqual(len(apps.rows()), 1)
        self.assertEqual(self.ledger.balance('user')['reserved'], 1)

    def test_cap_failure_package_does_not_reuse_stale_profile(self):
        from jobpilot.applications import Applications
        import copy
        self.automation.settings(daily_cap=1)
        lane = self.automation.save_lane({'name': 'Review', 'mode': 'review-first'})
        self.automation.activate(lane['id'], True)
        self.ledger.reserve('user', 'occupied-slot', self.automation.preview()['day'], 1)
        self.automation.run()
        paused_id = Applications(self.store).rows()[0]['id']
        updated = copy.deepcopy(PROFILE)
        updated['summary'] = 'Corrected source facts.'
        write_json(self.root / 'profile.json', updated)
        self.ledger.settle('user', 'occupied-slot', False)
        self.assertFalse(self.automation.preview()['candidates'][0]['eligible'])
        self.assertEqual(self.automation.run(), [])
        self.assertFalse(Applications(self.store).get(paused_id)['document_approved'])

    def test_maximum_five_lanes_and_shared_cap(self):
        self.automation.settings(daily_cap=1, timezone_name='Europe/Warsaw')
        for n in range(5):
            lane = self.automation.save_lane({'name': str(n)})
            self.automation.activate(lane['id'], True)
        with self.assertRaises(ValueError):
            self.automation.save_lane({'name': 'six'})
        self.store.add({'url': 'https://example.com/other', 'title': 'React', 'company': 'Other', 'description': 'React'})
        self.assertEqual(len(self.automation.run()), 1)
        self.assertEqual(self.ledger.balance('user')['reserved'], 1)

    def test_timezone_preview_and_plan_gate(self):
        self.automation.settings(daily_cap=2, timezone_name='Europe/Warsaw')
        self.assertEqual(self.automation.preview(datetime(2026, 10, 15, 23, 0, tzinfo=timezone.utc))['day'], '2026-10-16')
        from jobpilot.automation import Automation
        locked = Automation(self.store, self.ledger, 'user', entitled=False)
        lane = locked.save_lane({'name': 'No plan'})
        with self.assertRaises(ValueError):
            locked.activate(lane['id'], True)

    def test_automatic_requires_supported_adapter_and_pauses_missing_answers(self):
        lane = self.automation.save_lane({'name': 'Automatic', 'mode': 'automatic'})
        self.automation.activate(lane['id'], True)
        results = self.automation.run()
        self.assertEqual(results, [])
        self.assertIn('supported adapter', self.automation.preview()['candidates'][0]['reason'])

    def test_named_profile_lane_uses_its_source_and_stales_on_edit(self):
        from jobpilot.profile_vault import ProfileVault
        from jobpilot.applications import Applications
        import copy
        vault = ProfileVault(self.root)
        vault.save(PROFILE, name='Default')
        other = copy.deepcopy(PROFILE)
        other['summary'] = 'My second verified profile.'
        selected = vault.save(other, name='Second')
        lane = self.automation.save_lane({'name': 'Second', 'resume_profile': selected['id']})
        self.automation.activate(lane['id'], True)
        identifier = self.automation.run()[0]
        apps = Applications(self.store)
        run = apps.get(identifier)
        self.assertEqual(apps.snapshot(identifier)['profile']['summary'], other['summary'])
        apps.assert_current(run)
        other['summary'] = 'Corrected second profile.'
        vault.save(other, selected['id'], selected['version'])
        with self.assertRaises(ValueError):
            apps.approve_documents(identifier, run['document_hash'])
