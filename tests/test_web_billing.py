import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask, g
from flask_wtf.csrf import CSRFProtect

class BillingRouteTests(unittest.TestCase):
    def setUp(self):
        from jobpilot.web_billing import register_billing
        from jobpilot.accounts import Accounts
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'jobpilot/web_templates'))
        self.app.config.update(TESTING=True, SECRET_KEY='test', JOBPILOT_HOME=self.tmp.name,
            BILLING_PUBLIC_URL='https://app.example', STRIPE_SECRET_KEY='test-provider-key', STRIPE_WEBHOOK_SECRET='test-webhook',
            BILLING_FX_RATE={'pln_per_usd': '4', 'date':'2026-10-15', 'source':'fixture'})
        accounts = Accounts(self.tmp.name)
        self.account = accounts.identity('email','person@example.test', email='person@example.test', verified=True)
        self.app.extensions['jobpilot_accounts'] = accounts
        @self.app.before_request
        def identity():
            g.jobpilot_account = self.account
        CSRFProtect(self.app)
        self.app.config['WTF_CSRF_ENABLED'] = False
        register_billing(self.app)
        self.client = self.app.test_client()

    def test_checkout_uses_server_price_and_reuses_attempt(self):
        with patch('jobpilot.web_billing.stripe_request', return_value={'id':'cs_test', 'url':'https://checkout.stripe.com/c/test'}) as provider:
            payload = {'kind':'subscription', 'tier':'starter', 'cycle':'annual', 'attempt':'one-attempt', 'amount':'1', 'owner':'true'}
            first = self.client.post('/billing/checkout', data=payload)
            second = self.client.post('/billing/checkout', data=payload)
        self.assertEqual(first.status_code,303)
        self.assertEqual(second.status_code,303)
        self.assertEqual(provider.call_count,1)
        self.assertEqual(provider.call_args.args[1]['line_items[0][price_data][unit_amount]'],'16400')
        self.assertEqual(self.app.extensions['jobpilot_billing'].balance(self.account['id'])['available'],0)

    def test_public_customer_cannot_use_test_checkout_without_operator_allowlist(self):
        self.app.config['STRIPE_SECRET_KEY'] = 'sk_test_fixture'
        with patch('jobpilot.web_billing.stripe_request',return_value={'id':'cs_test_blocked','url':'https://checkout.stripe.com/c/test'}) as provider:
            response = self.client.post('/billing/checkout',data={'tier':'starter','attempt':'test-blocked'})
        self.assertEqual(response.status_code,403)
        provider.assert_not_called()
        self.app.config['BILLING_TEST_ACCOUNT_IDS'] = self.account['id']
        with patch('jobpilot.web_billing.stripe_request',return_value={'id':'cs_test_allowed','url':'https://checkout.stripe.com/c/test'}) as provider:
            response=self.client.post('/billing/checkout',data={'tier':'starter','attempt':'test-allowed'})
        self.assertEqual(response.status_code,303)
        self.assertEqual(provider.call_count,1)

    def test_missing_config_and_owner_never_calls_provider(self):
        self.app.config['STRIPE_SECRET_KEY']=''
        with patch('jobpilot.web_billing.stripe_request') as provider:
            self.assertEqual(self.client.post('/billing/checkout',data={'tier':'starter','cycle':'monthly'}).status_code,503)
            self.account = {'id':'owner', 'role':'owner'}
            self.assertEqual(self.client.post('/billing/checkout',data={'tier':'starter','cycle':'monthly'}).status_code,303)
            provider.assert_not_called()

    def test_wallet_markup_is_not_awarded_and_no_success_url_grant(self):
        with patch('jobpilot.web_billing.stripe_request', return_value={'id':'cs_wallet','url':'https://checkout.stripe.com/c/test'}) as provider:
            self.client.post('/billing/checkout',data={'kind':'wallet','base_usd':'20','attempt':'wallet-attempt'})
        self.assertEqual(provider.call_args.args[1]['line_items[0][price_data][unit_amount]'],'2500')
        ledger=self.app.extensions['jobpilot_billing']
        with ledger.connect() as db:
            self.assertEqual(db.execute('SELECT credits FROM checkout_orders').fetchone()[0],200)
        self.client.get('/billing?success=1')
        self.assertEqual(ledger.balance(self.account['id'])['available'],0)

    def test_webhook_requires_signature_even_without_csrf(self):
        self.app.config['WTF_CSRF_ENABLED']=True
        response=self.client.post('/billing/webhook',json={'id':'forged'})
        self.assertEqual(response.status_code,400)

    def test_signed_webhook_settles_wallet_once_without_browser_session(self):
        import hashlib,hmac,json,time
        with patch('jobpilot.web_billing.stripe_request', return_value={'id':'cs_wallet','url':'https://checkout.stripe.com/c/test'}):
            self.client.post('/billing/checkout',data={'kind':'wallet','base_usd':'20','attempt':'wallet-attempt'})
        event={'id':'evt1','created':int(time.time()),'type':'checkout.session.completed','data':{'object':{'id':'cs_wallet','payment_status':'paid','amount_total':2500,'currency':'usd','customer':'cus_one'}}}
        body=json.dumps(event).encode()
        stamp=str(int(time.time()))
        digest=hmac.new(b'test-webhook',stamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        self.app.config['WTF_CSRF_ENABLED']=True
        for _ in range(2):
            response=self.client.post('/billing/webhook',data=body,headers={'Stripe-Signature':f't={stamp},v1={digest}'})
            self.assertEqual(response.status_code,204)
        self.assertEqual(self.app.extensions['jobpilot_billing'].balance(self.account['id'])['available'],200)
        # Browser payment routes retain CSRF protection.
        self.assertEqual(self.client.post('/billing/checkout',data={'kind':'wallet'}).status_code,400)

    def test_customer_can_cancel_through_portal_when_fx_configuration_missing(self):
        ledger=self.app.extensions['jobpilot_billing']
        with ledger.connect() as db:
            db.execute('INSERT INTO subscriptions(account_id,tier,status,customer_id) VALUES(?,?,?,?)',(self.account['id'],'starter','active','cus_own'))
        self.app.config['BILLING_FX_RATE']=None
        with patch('jobpilot.web_billing.stripe_request',return_value={'url':'https://billing.stripe.com/p/session'}) as provider:
            response=self.client.post('/billing/portal',data={'customer':'cus_attacker'})
        self.assertEqual(response.status_code,303)
        self.assertEqual(provider.call_args.args[1]['customer'],'cus_own')

    def test_pending_subscription_is_shared_across_different_attempts(self):
        with patch('jobpilot.web_billing.stripe_request',side_effect=[{'id':'cs_pending','url':'https://checkout.stripe.com/c/pending'},{'id':'cs_duplicate','url':'https://checkout.stripe.com/c/duplicate'}]) as provider:
            first=self.client.post('/billing/checkout',data={'tier':'starter','cycle':'monthly','attempt':'first-tab'})
            second=self.client.post('/billing/checkout',data={'tier':'power','cycle':'annual','attempt':'second-tab'})
        self.assertEqual(first.status_code,303)
        self.assertEqual(second.status_code,303)
        self.assertEqual(second.headers['Location'],first.headers['Location'])
        self.assertEqual(provider.call_count,1)

    def test_pending_subscription_does_not_block_wallet_topup(self):
        with patch('jobpilot.web_billing.stripe_request',side_effect=[{'id':'cs_pending','url':'https://checkout.stripe.com/c/pending'},{'id':'cs_wallet','url':'https://checkout.stripe.com/c/wallet'}]) as provider:
            self.client.post('/billing/checkout',data={'tier':'starter','attempt':'subscription'})
            response=self.client.post('/billing/checkout',data={'kind':'wallet','base_usd':'20','attempt':'wallet'})
        self.assertEqual(response.status_code,303)
        self.assertEqual(provider.call_count,2)

    def test_expired_subscription_checkout_releases_account_slot(self):
        from jobpilot.payment_events import apply_event
        with patch('jobpilot.web_billing.stripe_request',side_effect=[{'id':'cs_old','url':'https://checkout.stripe.com/c/old'},{'id':'cs_new','url':'https://checkout.stripe.com/c/new'}]) as provider:
            self.client.post('/billing/checkout',data={'tier':'starter','attempt':'old'})
            apply_event(self.app.extensions['jobpilot_billing'],{'id':'expired','type':'checkout.session.expired','data':{'object':{'id':'cs_old'}}},lambda key:True)
            response=self.client.post('/billing/checkout',data={'tier':'power','attempt':'new'})
        self.assertEqual(response.status_code,303)
        self.assertEqual(provider.call_count,2)

    def test_unknown_provider_timeout_keeps_account_slot_reserved(self):
        from jobpilot.web_billing import BillingUnavailable
        with patch('jobpilot.web_billing.stripe_request',side_effect=BillingUnavailable('timeout')) as provider:
            self.assertEqual(self.client.post('/billing/checkout',data={'tier':'starter','attempt':'original'}).status_code,503)
            self.assertEqual(self.client.post('/billing/checkout',data={'tier':'starter','attempt':'new-tab'}).status_code,409)
            self.assertEqual(provider.call_count,1)
