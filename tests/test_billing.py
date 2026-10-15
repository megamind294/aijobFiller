import tempfile
import unittest
from pathlib import Path


class BillingTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.billing import CreditLedger
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = CreditLedger(Path(self.tmp.name))

    def test_duplicate_payment_grants_credits_once(self):
        self.ledger.grant('customer', 'invoice:1', 600, 'subscription')
        self.ledger.grant('customer', 'invoice:1', 600, 'subscription')
        self.assertEqual(self.ledger.balance('customer')['available'], 600)

    def test_reservation_charge_and_refund_are_idempotent(self):
        self.ledger.grant('customer', 'wallet:1', 2, 'wallet')
        self.ledger.reserve('customer', 'application:1', '2026-10-15', 10)
        self.assertEqual(self.ledger.balance('customer')['available'], 1)
        self.ledger.settle('customer', 'application:1', success=True)
        self.ledger.settle('customer', 'application:1', success=True)
        self.assertEqual(self.ledger.balance('customer')['available'], 1)
        self.ledger.refund('customer', 'application:1')
        self.ledger.refund('customer', 'application:1')
        self.assertEqual(self.ledger.balance('customer')['available'], 2)

    def test_daily_cap_is_shared_across_lanes_and_account_scoped(self):
        self.ledger.grant('customer', 'wallet:1', 3, 'wallet')
        self.ledger.reserve('customer', 'lane-a:job1', '2026-10-15', 1)
        with self.assertRaises(ValueError):
            self.ledger.reserve('customer', 'lane-b:job2', '2026-10-15', 1)
        with self.assertRaises(ValueError):
            self.ledger.settle('other-account', 'lane-a:job1', success=True)
        self.ledger.settle('customer', 'lane-a:job1', success=False)
        self.ledger.reserve('customer', 'lane-b:job2', '2026-10-15', 1)

    def test_dated_rate_prices_markup_per_package_not_per_month(self):
        from jobpilot.billing import catalog
        with self.assertRaises(ValueError):
            catalog(None)
        prices = catalog({'pln_per_usd': '4.0', 'date': '2026-10-15', 'source': 'test fixture'})
        self.assertEqual(prices['starter']['annual']['usd'], '164.00')
        self.assertEqual(prices['starter']['monthly']['usd'], '24.00')

    def test_refund_preserves_original_expiry(self):
        import time
        self.ledger.grant('customer','expires',1,'subscription',expires=time.time()+100)
        self.ledger.reserve('customer','expiring-app','2026-10-15',10)
        self.ledger.settle('customer','expiring-app',True)
        self.ledger.refund('customer','expiring-app')
        with self.ledger.connect() as db:
            row=db.execute("SELECT expires FROM credit_buckets WHERE grant_key='refund:expiring-app'").fetchone()
        self.assertIsNotNone(row['expires'])

    def test_prepaid_subscription_refreshes_monthly_without_rollover(self):
        from datetime import datetime, timezone
        start=datetime(2026,1,31,tzinfo=timezone.utc).timestamp()
        end=datetime(2026,4,30,tzinfo=timezone.utc).timestamp()
        with self.ledger.connect() as db:
            db.execute('INSERT INTO subscriptions(account_id,tier,status,subscription_id,paid_start,paid_through) VALUES(?,?,?,?,?,?)',('customer','starter','active','sub1',start,end))
        self.ledger.refresh('customer',stamp=start+1)
        self.ledger.refresh('customer',stamp=datetime(2026,3,1,tzinfo=timezone.utc).timestamp())
        self.ledger.refresh('customer',stamp=datetime(2026,3,1,tzinfo=timezone.utc).timestamp())
        with self.ledger.connect() as db:
            rows=db.execute("SELECT * FROM credit_buckets WHERE kind='subscription'").fetchall()
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['expires'],datetime(2026,2,28,tzinfo=timezone.utc).timestamp())

    def test_invalid_dated_rate_cannot_publish_prices(self):
        from jobpilot.billing import catalog
        with self.assertRaises(ValueError):
            catalog({'pln_per_usd':'4','date':'yesterday','source':'fixture'})
