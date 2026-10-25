"""Owner-only customer records; never expose authentication or provider secrets."""
import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from flask import abort, g, render_template, request


PURCHASE_TYPES = ('invoice.paid', 'invoice.payment_succeeded',
                  'checkout.session.completed', 'checkout.session.async_payment_succeeded')


def customer_records(root, page=1, search='', purchase='all'):
    root = Path(root).resolve()
    with closing(sqlite3.connect((root / 'accounts.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('ATTACH DATABASE ? AS billing', ((root / 'billing.sqlite3').as_uri() + '?mode=ro',))
        marks = ','.join('?' for _ in PURCHASE_TYPES)
        purchased = f"EXISTS(SELECT 1 FROM billing.billing_events e WHERE e.account_id=a.id AND e.kind IN ({marks}))"
        where = "a.role='customer' AND (a.email LIKE ? OR a.id LIKE ?)"
        params = ['%' + search + '%'] * 2
        if purchase in ('yes', 'no'):
            where += ' AND ' + ('' if purchase == 'yes' else 'NOT ') + purchased
            params.extend(PURCHASE_TYPES)
        total = db.execute('SELECT COUNT(*) FROM accounts a WHERE ' + where, params).fetchone()[0]
        rows = db.execute('''SELECT a.id,a.email,a.created,s.tier,s.status
            FROM accounts a LEFT JOIN billing.subscriptions s ON s.account_id=a.id
            WHERE ''' + where + ' ORDER BY a.created DESC,a.id LIMIT 50 OFFSET ?', [*params, (page-1)*50]).fetchall()
        result = []
        for row in rows:
            events = db.execute(f'SELECT payload FROM billing.billing_events WHERE account_id=? AND kind IN ({marks})',
                                [row['id'], *PURCHASE_TYPES]).fetchall()
            modes = set()
            for event in events:
                try:
                    value = json.loads(event['payload']).get('livemode')
                    if type(value) is bool:
                        modes.add(value)
                except (ValueError, AttributeError):
                    pass
            payment = ('Live payment' if True in modes else 'Sandbox payment' if False in modes
                       else 'Mode not recorded') if events else 'No purchase'
            buckets = db.execute("""SELECT b.kind,b.credits,
                (SELECT COUNT(*) FROM billing.credit_reservations r WHERE r.bucket_id=b.id AND r.state='reserved') AS reserved
                FROM billing.credit_buckets b WHERE b.account_id=? AND b.kind!='free' AND (b.expires IS NULL OR b.expires>?)""",
                                 (row['id'], time.time())).fetchall()
            result.append({**dict(row), 'purchased': bool(events), 'payment': payment,
                'created_date': datetime.fromtimestamp(row['created'], timezone.utc).strftime('%Y-%m-%d'),
                'available': sum(max(0, b['credits']-b['reserved']) for b in buckets),
                'trial_remaining': sum(max(0, b['credits']-b['reserved']) for b in buckets if b['kind']=='trial')})
        return result, total


def register_customer_admin(app):
    @app.get('/admin/customers')
    def admin_customers():
        if getattr(g, 'jobpilot_account', {}).get('role') != 'owner':
            abort(403)
        try:
            page = max(1, min(1000000, int(request.args.get('page', '1'))))
        except ValueError:
            abort(400)
        search = request.args.get('q', '').strip()[:100]
        purchase = request.args.get('purchase', 'all')
        if purchase not in ('all', 'yes', 'no'):
            abort(400)
        rows, total = customer_records(app.config['JOBPILOT_HOME'], page, search, purchase)
        return render_template('customers.html', customers=rows, total=total, page=page,
                               search=search, purchase=purchase, has_next=page*50<total)
