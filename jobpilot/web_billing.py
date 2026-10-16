"""Hosted customer billing. Configuration never triggers a provider operation."""
import hashlib
import json
import os
import secrets
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import abort, g, redirect, render_template, request, url_for

from .billing import CreditLedger, TIERS, catalog
from .payment_events import apply_event, verify_event


class BillingUnavailable(Exception):
    pass


def stripe_request(path, fields, *, key, idempotency=None):
    headers = {'Authorization':'Bearer ' + key, 'Content-Type':'application/x-www-form-urlencoded',
               'Stripe-Version':'2025-02-24.acacia'}
    if idempotency:
        headers['Idempotency-Key'] = idempotency
    req = Request('https://api.stripe.com/v1/' + path, data=urlencode(fields).encode(), headers=headers, method='POST')
    try:
        with urlopen(req, timeout=20) as response:
            result=json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        raise BillingUnavailable('The payment provider is unavailable. Retry this checkout attempt.') from None
    if not isinstance(result, dict):
        raise BillingUnavailable('Invalid payment provider response.')
    return result


def register_billing(app):
    ledger = CreditLedger(app.config['JOBPILOT_HOME'])
    app.extensions['jobpilot_billing'] = ledger
    app.extensions['jobpilot_credit_ledger'] = ledger

    def setting(name):
        return app.config.get(name, os.environ.get(name, ''))

    def pricing():
        rate = app.config.get('BILLING_FX_RATE')
        if rate is None:
            try:
                rate=json.loads((Path(app.config['JOBPILOT_HOME']) / 'billing-fx.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                rate=None
        return catalog(rate)

    def configured(require_prices=True):
        base=setting('BILLING_PUBLIC_URL')
        parsed=urlsplit(base)
        if not setting('STRIPE_SECRET_KEY') or (require_prices and not setting('STRIPE_WEBHOOK_SECRET')) or parsed.scheme != 'https' or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise BillingUnavailable('Billing configuration required: Stripe keys, webhook signing secret, public HTTPS URL and dated exchange rate.')
        if not require_prices:
            return base.rstrip('/'), {}
        try:
            prices=pricing()
        except ValueError as exc:
            raise BillingUnavailable(str(exc)) from None
        return base.rstrip('/'), prices

    def account():
        value=getattr(g,'jobpilot_account',None)
        if not value or not value.get('id'):
            abort(401)
        return value

    def hosted_url(value, hostname):
        parsed=urlsplit(value or '')
        if parsed.scheme != 'https' or parsed.hostname != hostname:
            raise BillingUnavailable('Invalid hosted payment URL.')
        return value

    @app.get('/billing')
    def billing():
        who=account()
        owner=who.get('role') == 'owner'
        error=None
        try:
            _, prices=configured()
        except BillingUnavailable as exc:
            error=str(exc)
            try:
                prices=pricing()
            except ValueError:
                prices={}
        return render_template('billing.html', prices=prices, configuration_error=error, owner=owner,
            paid_access=owner or ledger.has_paid_access(who['id']),
            test_payments=setting('STRIPE_SECRET_KEY').startswith(('sk_test_', 'rk_test_')),
            balance=ledger.balance(who['id']), subscription=ledger.subscription(who['id']), history=ledger.history(who['id']), attempt=secrets.token_urlsafe(24))

    @app.post('/billing/checkout')
    def billing_checkout():
        who=account()
        if who.get('role') == 'owner':
            return redirect(url_for('billing'),code=303)
        try:
            base, prices=configured()
            kind=request.form.get('kind','subscription')
            tier=request.form.get('tier','')
            cycle=request.form.get('cycle','monthly')
            if kind == 'subscription':
                if tier not in prices or cycle not in prices[tier]:
                    abort(400)
                existing=ledger.subscription(who['id'])
                if existing.get('subscription_id') and existing['status'] not in ('canceled','incomplete_expired'):
                    return 'Manage the existing subscription in the customer portal.',409
                amount=int(Decimal(prices[tier][cycle]['usd'])*100)
                credits=TIERS[tier]['credits']
                label='JobPilot ' + tier.title() + ' / ' + cycle
            elif kind == 'wallet':
                try:
                    value=Decimal(request.form.get('base_usd','5'))
                    if not value.is_finite() or value < 5 or value > 1000 or value != value.quantize(Decimal('.01')):
                        raise ValueError
                    # Wallet credit unit is $0.10; reject partial-credit amounts.
                    if value * 10 != (value * 10).to_integral_value():
                        raise ValueError
                except (InvalidOperation, ValueError):
                    abort(400)
                markup=Decimal(prices['starter']['monthly']['usd'])-Decimal(TIERS['starter']['monthly'])
                amount=int((value+markup).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)*100)
                credits=int(value*10)
                tier=cycle=''
                label='JobPilot wallet: ' + str(credits) + ' application credits'
            else:
                abort(400)
            nonce=request.form.get('attempt','') or secrets.token_urlsafe(24)
            if len(nonce)>200:
                abort(400)
            attempt=hashlib.sha256((who['id']+':'+nonce).encode()).hexdigest()
            fields={'mode':'subscription' if kind=='subscription' else 'payment',
                'success_url':base+'/billing?success=1','cancel_url':base+'/billing?canceled=1',
                'client_reference_id':who['id'], 'line_items[0][quantity]':'1',
                'line_items[0][price_data][currency]':'usd','line_items[0][price_data][unit_amount]':str(amount),
                'line_items[0][price_data][product_data][name]':label,
                'metadata[account_id]':who['id'],'metadata[order_attempt]':attempt}
            if kind=='subscription':
                fields.update({'line_items[0][price_data][recurring][interval]':'month',
                    'line_items[0][price_data][recurring][interval_count]':str({'monthly':1,'quarterly':3,'annual':12}[cycle]),
                    'subscription_data[metadata][account_id]':who['id'],'subscription_data[metadata][order_attempt]':attempt})
            else:
                fields['customer_creation']='always'
            subscription=ledger.subscription(who['id'])
            if subscription.get('customer_id'):
                fields['customer']=subscription['customer_id']
                fields.pop('customer_creation',None)
            elif who.get('email'):
                fields['customer_email']=who['email']
            # Persist the price snapshot before network I/O. A provider timeout
            # can be retried with the same stable Stripe idempotency key.
            with ledger.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if kind == 'subscription':
                    # Account-wide slot survives network timeouts and serializes
                    # separate tabs/workers before any provider call is made.
                    active = db.execute('SELECT subscription_id,status FROM subscriptions WHERE account_id=?', (who['id'],)).fetchone()
                    if active and active['subscription_id'] and active['status'] not in ('canceled', 'incomplete_expired'):
                        return 'Manage the existing subscription in the customer portal.',409
                    pending = db.execute('SELECT o.* FROM subscription_checkouts p JOIN checkout_orders o ON o.attempt=p.attempt WHERE p.account_id=?', (who['id'],)).fetchone()
                    if pending and pending['attempt'] != attempt:
                        if pending['url']:
                            return redirect(hosted_url(pending['url'],'checkout.stripe.com'),code=303)
                        return 'A subscription checkout is already pending. Retry the original checkout attempt.',409
                    db.execute('INSERT OR IGNORE INTO subscription_checkouts VALUES(?,?)', (who['id'], attempt))
                old=db.execute('SELECT * FROM checkout_orders WHERE attempt=?',(attempt,)).fetchone()
                if old and (old['account_id'],old['kind'],old['tier'],old['cycle'],old['amount']) != (who['id'],kind,tier,cycle,amount):
                    abort(409)
                db.execute('INSERT OR IGNORE INTO checkout_orders(attempt,account_id,kind,tier,cycle,credits,amount,created) VALUES(?,?,?,?,?,?,?,?)',
                    (attempt,who['id'],kind,tier,cycle,credits,amount,time.time()))
            with ledger.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old=db.execute('SELECT * FROM checkout_orders WHERE attempt=?',(attempt,)).fetchone()
                if old['url']:
                    return redirect(hosted_url(old['url'],'checkout.stripe.com'),code=303)
                result=stripe_request('checkout/sessions',fields,key=setting('STRIPE_SECRET_KEY'),idempotency='jobpilot:'+attempt)
                destination=hosted_url(result.get('url'),'checkout.stripe.com')
                if not isinstance(result.get('id'),str):
                    raise BillingUnavailable('Invalid checkout session.')
                db.execute('UPDATE checkout_orders SET session_id=?,url=? WHERE attempt=?',(result['id'],destination,attempt))
            return redirect(destination,code=303)
        except BillingUnavailable as exc:
            return str(exc),503

    @app.post('/billing/portal')
    def billing_portal():
        who=account()
        if who.get('role')=='owner':
            return redirect(url_for('billing'),code=303)
        try:
            base,_=configured(require_prices=False)
            customer=ledger.subscription(who['id']).get('customer_id')
            if not customer:
                return 'A completed customer checkout is required before opening the billing portal.',409
            result=stripe_request('billing_portal/sessions',{'customer':customer,'return_url':base+'/billing'},key=setting('STRIPE_SECRET_KEY'))
            return redirect(hosted_url(result.get('url'),'billing.stripe.com'),code=303)
        except BillingUnavailable as exc:
            return str(exc),503

    @app.post('/billing/webhook')
    def billing_webhook():
        secret=setting('STRIPE_WEBHOOK_SECRET')
        if not secret:
            return 'Payment webhook configuration required.',503
        try:
            event=verify_event(request.get_data(),request.headers.get('Stripe-Signature',''),secret)
        except ValueError:
            return 'Invalid payment signature or payload.',400
        try:
            accounts=app.extensions['jobpilot_accounts']
            apply_event(ledger,event,lambda key: bool(accounts.get(key)))
        except (ValueError, KeyError, TypeError):
            # Do not acknowledge a settlement that could not be committed.
            return 'Payment event could not be settled.',500
        return '',204

    if app.extensions.get('csrf'):
        app.extensions['csrf'].exempt(billing_webhook)
