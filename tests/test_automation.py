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
