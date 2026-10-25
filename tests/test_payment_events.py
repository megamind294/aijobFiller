import hashlib
import hmac
import json
import tempfile
import time
import unittest
from pathlib import Path


class PaymentTests(unittest.TestCase):
    def test_payment_event_preserves_test_mode_for_owner_reporting(self):
        from jobpilot.billing import CreditLedger
        from jobpilot.payment_events import apply_event
        with tempfile.TemporaryDirectory() as temporary:
            ledger = CreditLedger(temporary)
            apply_event(ledger, {'id':'evt_mode', 'type':'fixture', 'livemode':False,
                                'data':{'object':{'id':'object_mode'}}}, lambda account:False)
            with ledger.connect() as db:
                payload = json.loads(db.execute('SELECT payload FROM billing_events').fetchone()[0])
            self.assertIs(payload['livemode'], False)

    def test_signature_is_required_and_timestamp_bounded(self):
        from jobpilot.payment_events import verify_event
        body = b'{"id":"event1","type":"test"}'
        stamp = int(time.time())
        signature = hmac.new(b'webhook-secret', str(stamp).encode() + b'.' + body, hashlib.sha256).hexdigest()
        self.assertEqual(verify_event(body, f't={stamp},v1={signature}', 'webhook-secret')['id'], 'event1')
        for header in ('', f't={stamp},v1=incorrect', f't={stamp-301},v1={signature}'):
            with self.assertRaises(ValueError):
                verify_event(body, header, 'webhook-secret')

    def test_paid_checkout_is_settled_once_and_unpaid_does_not_grant(self):
        from jobpilot.billing import CreditLedger
        from jobpilot.payment_events import apply_event
        with tempfile.TemporaryDirectory() as temporary:
            ledger = CreditLedger(Path(temporary))
            with ledger.connect() as db:
                db.execute('INSERT INTO checkout_orders(attempt,account_id,kind,tier,cycle,credits,amount,session_id,created) VALUES(?,?,?,?,?,?,?,?,?)',('attempt','customer','wallet','','',600,6500,'cs1',time.time()))
            event = {'id': 'evt1', 'type': 'checkout.session.completed', 'data': {'object': {
                'id': 'cs1', 'amount_total':6500, 'currency':'usd', 'payment_status': 'unpaid', 'metadata': {'account_id': 'customer', 'credits': '600', 'kind': 'subscription', 'tier': 'starter'}}}}
            apply_event(ledger, event, account_exists=lambda x: x == 'customer')
            self.assertEqual(ledger.balance('customer')['available'], 0)
            event['id'] = 'evt2'
            event['data']['object']['payment_status'] = 'paid'
            apply_event(ledger, event, account_exists=lambda x: x == 'customer')
            event['id'] = 'evt3'
            apply_event(ledger, event, account_exists=lambda x: x == 'customer')
            self.assertEqual(ledger.balance('customer')['available'], 600)

    def test_invoice_dedup_and_stale_subscription_event(self):
        from jobpilot.billing import CreditLedger
        from jobpilot.payment_events import apply_event
        with tempfile.TemporaryDirectory() as temporary:
            ledger=CreditLedger(temporary)
            with ledger.connect() as db:
                db.execute('INSERT INTO subscriptions(account_id,tier,status,customer_id,subscription_id) VALUES(?,?,?,?,?)',('customer','starter','active','cus1','sub1'))
            now=int(time.time())
            invoice={'id':'evt-invoice','created':now,'type':'invoice.paid','data':{'object':{'id':'in1','paid':True,'subscription':'sub1','customer':'cus1','period_start':now,'period_end':now+90*86400}}}
            apply_event(ledger,invoice,lambda x:x=='customer')
            invoice['id']='another-invoice-event'
            apply_event(ledger,invoice,lambda x:x=='customer')
            self.assertEqual(ledger.balance('customer')['available'],600)
            deleted={'id':'evt-del','created':now+2,'type':'customer.subscription.deleted','data':{'object':{'id':'sub1','status':'canceled'}}}
            apply_event(ledger,deleted,lambda x:True)
            deleted.update(id='evt-old',created=now+1,type='customer.subscription.updated')
            deleted['data']['object']['status']='active'
            apply_event(ledger,deleted,lambda x:True)
            self.assertEqual(ledger.subscription('customer')['status'],'canceled')

    def test_deletion_before_checkout_cannot_resurrect_subscription(self):
        from jobpilot.billing import CreditLedger
        from jobpilot.payment_events import apply_event
        with tempfile.TemporaryDirectory() as temporary:
            ledger=CreditLedger(temporary)
            now=int(time.time())
            apply_event(ledger,{'id':'deleted-first','created':now+10,'type':'customer.subscription.deleted','data':{'object':{'id':'sub1','status':'canceled'}}},lambda x:True)
            with ledger.connect() as db:
                db.execute('INSERT INTO checkout_orders(attempt,account_id,kind,tier,cycle,credits,amount,session_id,created) VALUES(?,?,?,?,?,?,?,?,?)',('one','customer','subscription','starter','monthly',600,2400,'cs1',now))
            apply_event(ledger,{'id':'checkout-late','created':now,'type':'checkout.session.completed','data':{'object':{'id':'cs1','amount_total':2400,'currency':'usd','payment_status':'paid','customer':'cus1','subscription':'sub1'}}},lambda x:True)
            invoice={'id':'invoice-late','created':now+1,'type':'invoice.paid','data':{'object':{'id':'in1','paid':True,'subscription':'sub1','customer':'cus1','period_start':now,'period_end':now+30*86400}}}
            apply_event(ledger,invoice,lambda x:True)
            self.assertEqual(ledger.balance('customer')['available'],0)
            self.assertEqual(ledger.subscription('customer')['status'],'canceled')

    def test_older_paid_invoice_does_not_overwrite_newer_past_due_status(self):
        from jobpilot.billing import CreditLedger
        from jobpilot.payment_events import apply_event
        with tempfile.TemporaryDirectory() as temporary:
            ledger=CreditLedger(temporary)
            now=int(time.time())
            with ledger.connect() as db:
                db.execute('INSERT INTO subscriptions(account_id,tier,status,customer_id,subscription_id) VALUES(?,?,?,?,?)',('customer','starter','active','cus1','sub1'))
            apply_event(ledger,{'id':'latest-status','created':now+10,'type':'customer.subscription.updated','data':{'object':{'id':'sub1','status':'past_due'}}},lambda x:True)
            apply_event(ledger,{'id':'old-invoice','created':now,'type':'invoice.paid','data':{'object':{'id':'in_old','paid':True,'subscription':'sub1','customer':'cus1','period_start':now,'period_end':now+30*86400}}},lambda x:True)
            self.assertEqual(ledger.subscription('customer')['status'],'past_due')
