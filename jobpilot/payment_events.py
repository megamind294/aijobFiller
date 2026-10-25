"""Signed Stripe snapshot events, committed atomically with their entitlements."""
import hashlib
import hmac
import json
import time

from .billing import TIERS


def verify_event(body, signature, secret, stamp=None):
    if not secret or len(body) > 1024 * 1024:
        raise ValueError('Invalid payment signature.')
    values = {}
    for item in signature.split(','):
        key, _, value = item.strip().partition('=')
        values.setdefault(key, []).append(value)
    try:
        timestamp = int(values['t'][0])
    except (ValueError, KeyError, IndexError):
        raise ValueError('Invalid payment signature.') from None
    if abs((time.time() if stamp is None else stamp) - timestamp) > 300:
        raise ValueError('Expired payment signature.')
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b'.' + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(digest, candidate) for candidate in values.get('v1', [])):
        raise ValueError('Invalid payment signature.')
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('Invalid payment event.') from None
    if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not isinstance(event.get('data', {}), dict):
        raise ValueError('Invalid payment event.')
    return event


def identifier(value):
    return value.get('id') if isinstance(value, dict) else value


def apply_event(ledger, event, account_exists):
    kind = event.get('type', '')
    obj = event.get('data', {}).get('object', {})
    if not isinstance(obj, dict):
        raise ValueError('Invalid payment object.')
    account = ''
    stamp = event.get('created', 0)
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM billing_events WHERE event_id=?', (event['id'],)).fetchone():
            return
        if kind in ('checkout.session.completed', 'checkout.session.async_payment_succeeded'):
            order = db.execute('SELECT * FROM checkout_orders WHERE session_id=?', (obj.get('id'),)).fetchone()
            if order and obj.get('payment_status') == 'paid':
                account = order['account_id']
                if not account_exists(account) or account == 'owner':
                    raise ValueError('Payment account could not be verified.')
                if obj.get('amount_total') != order['amount'] or obj.get('currency') != 'usd':
                    raise ValueError('Checkout amount does not match its server order.')
                if order['kind'] == 'wallet':
                    db.execute('INSERT OR IGNORE INTO credit_buckets(account_id,grant_key,kind,credits,expires,created) VALUES(?,?,?,?,?,?)',
                               (account, 'checkout:' + obj['id'], 'wallet', order['credits'], None, time.time()))
                    db.execute('INSERT INTO subscriptions(account_id,tier,status,customer_id) VALUES(?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET customer_id=excluded.customer_id',
                               (account, 'free', 'active', identifier(obj.get('customer'))))
                else:
                    # Invoice payment, rather than the browser or Checkout event,
                    # establishes the actual paid billing period.
                    db.execute('INSERT INTO subscriptions(account_id,tier,status,customer_id,subscription_id) VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET customer_id=excluded.customer_id,subscription_id=excluded.subscription_id,tier=excluded.tier',
                               (account, order['tier'], 'incomplete', identifier(obj.get('customer')), identifier(obj.get('subscription'))))
                    terminal = db.execute('SELECT terminal FROM provider_versions WHERE object_id=?', (identifier(obj.get('subscription')),)).fetchone()
                    if terminal and terminal['terminal']:
                        db.execute("UPDATE subscriptions SET status='canceled' WHERE account_id=?", (account,))
                    db.execute('DELETE FROM subscription_checkouts WHERE account_id=? AND attempt=?', (account, order['attempt']))
        elif kind in ('checkout.session.expired', 'checkout.session.async_payment_failed'):
            # A browser cancel URL is not evidence that Stripe closed a session.
            db.execute('DELETE FROM subscription_checkouts WHERE attempt IN (SELECT attempt FROM checkout_orders WHERE session_id=?)', (obj.get('id'),))
        elif kind in ('invoice.paid', 'invoice.payment_succeeded'):
            details = (obj.get('parent') or {}).get('subscription_details') or obj.get('subscription_details') or {}
            sub_id = identifier(obj.get('subscription') or details.get('subscription'))
            row = db.execute('SELECT * FROM subscriptions WHERE subscription_id=?', (sub_id,)).fetchone() if sub_id else None
            if not row and details.get('metadata', {}).get('order_attempt'):
                order = db.execute('SELECT * FROM checkout_orders WHERE attempt=? AND kind=?', (details['metadata']['order_attempt'], 'subscription')).fetchone()
                if order and account_exists(order['account_id']):
                    db.execute('INSERT INTO subscriptions(account_id,tier,status,customer_id,subscription_id) VALUES(?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET tier=excluded.tier,customer_id=excluded.customer_id,subscription_id=excluded.subscription_id',
                               (order['account_id'], order['tier'], 'incomplete', identifier(obj.get('customer')), sub_id))
                    row = db.execute('SELECT * FROM subscriptions WHERE subscription_id=?', (sub_id,)).fetchone()
            if row and obj.get('paid', kind == 'invoice.paid'):
                account = row['account_id']
                if not account_exists(account) or row['customer_id'] != identifier(obj.get('customer')):
                    raise ValueError('Invoice customer could not be verified.')
                # Subscription lines carry service periods; invoice.period_* can
                # instead describe the preceding invoicing period.
                periods = [line['period'] for line in (obj.get('lines') or {}).get('data', []) if line.get('period') and (line.get('type') == 'subscription' or (line.get('parent') or {}).get('type') == 'subscription_item_details')]
                period = max(periods, key=lambda p:p['end']) if periods else {'start':obj.get('period_start'), 'end':obj.get('period_end')}
                start, end = period['start'], period['end']
                if not isinstance(start, (int,float)) or not isinstance(end, (int,float)) or end <= start:
                    raise ValueError('Invoice service period is missing.')
                version = db.execute('SELECT * FROM provider_versions WHERE object_id=?', (sub_id,)).fetchone()
                already_paid = db.execute('SELECT 1 FROM paid_invoices WHERE invoice_id=?', (obj['id'],)).fetchone()
                if not already_paid and (not row['paid_through'] or end > row['paid_through']) and not (version and version['terminal']):
                    status = row['status'] if version and version['created'] > stamp else 'active'
                    db.execute('UPDATE subscriptions SET status=?,paid_start=?,paid_through=?,cycle_end=? WHERE account_id=?', (status, start, end, end, account))
                elif version and version['terminal']:
                    db.execute("UPDATE subscriptions SET status='canceled' WHERE account_id=?", (account,))
                db.execute('INSERT OR IGNORE INTO paid_invoices VALUES(?,?,?)', (obj['id'], sub_id, time.time()))
        elif kind in ('customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted'):
            sub_id = obj.get('id')
            row = db.execute('SELECT * FROM subscriptions WHERE subscription_id=?', (sub_id,)).fetchone()
            previous = db.execute('SELECT * FROM provider_versions WHERE object_id=?', (sub_id,)).fetchone()
            terminal = kind == 'customer.subscription.deleted'
            if not previous or (stamp >= previous['created'] and not previous['terminal']):
                status = 'canceled' if terminal else obj.get('status', 'incomplete')
                if row:
                    account = row['account_id']
                    db.execute('UPDATE subscriptions SET status=?,cycle_end=COALESCE(?,cycle_end) WHERE subscription_id=?', (status, obj.get('current_period_end'), sub_id))
                db.execute('INSERT INTO provider_versions VALUES(?,?,?) ON CONFLICT(object_id) DO UPDATE SET created=excluded.created,terminal=excluded.terminal', (sub_id, stamp, int(terminal)))
        db.execute('INSERT INTO billing_events VALUES(?,?,?,?,?)',
                   (event['id'], account, kind, json.dumps({'provider_object': obj.get('id', ''),
                    'livemode': event.get('livemode', obj.get('livemode'))}), time.time()))
    if account:
        ledger.refresh(account)
