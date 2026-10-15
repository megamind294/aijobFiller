"""Atomic application-credit accounting; no payment operation happens implicitly."""
import json
import sqlite3
import time
import calendar
from datetime import date, datetime, timezone
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TIERS = {
    'starter': {'credits': 600, 'auto': False, 'monthly': 19, 'quarterly': 49, 'annual': 159},
    'pro': {'credits': 1500, 'auto': False, 'monthly': 39, 'quarterly': 79, 'annual': 299},
    'power': {'credits': 4500, 'auto': True, 'monthly': 99, 'quarterly': 239, 'annual': 799},
}


def catalog(rate):
    try:
        value = Decimal(str(rate['pln_per_usd']))
        if not value.is_finite() or value <= 0 or not rate.get('date') or not rate.get('source'):
            raise ValueError
        date.fromisoformat(rate['date'])
        if not isinstance(rate['source'], str) or not rate['source'].strip():
            raise ValueError
    except (TypeError, KeyError, InvalidOperation, ValueError):
        raise ValueError('Publish a dated PLN-per-USD rate with its source before enabling checkout.') from None
    result = {}
    for name, tier in TIERS.items():
        result[name] = {cycle: {'usd': str((Decimal(base) + Decimal(20) / value).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
                                'base_usd': base, 'monthly_credits': tier['credits'], 'auto': tier['auto'], 'rate': dict(rate)}
                        for cycle, base in tier.items() if cycle in ('monthly', 'quarterly', 'annual')}
    return result


class CreditLedger:
    def __init__(self, root):
        self.path = Path(root) / 'billing.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS credit_buckets (
                    id INTEGER PRIMARY KEY, account_id TEXT, grant_key TEXT UNIQUE, kind TEXT,
                    credits INTEGER, expires REAL, created REAL);
                CREATE TABLE IF NOT EXISTS credit_reservations (
                    reference TEXT PRIMARY KEY, account_id TEXT, bucket_id INTEGER,
                    day TEXT, state TEXT, created REAL, updated REAL);
                CREATE TABLE IF NOT EXISTS billing_events (
                    event_id TEXT PRIMARY KEY, account_id TEXT, kind TEXT, payload TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS subscriptions (
                    account_id TEXT PRIMARY KEY, tier TEXT, status TEXT, customer_id TEXT,
                    subscription_id TEXT, cycle_end REAL);
                CREATE TABLE IF NOT EXISTS checkout_orders (
                    attempt TEXT PRIMARY KEY, account_id TEXT NOT NULL, kind TEXT NOT NULL,
                    tier TEXT, cycle TEXT, credits INTEGER NOT NULL, amount INTEGER NOT NULL,
                    session_id TEXT UNIQUE, url TEXT, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS provider_versions (
                    object_id TEXT PRIMARY KEY, created REAL NOT NULL, terminal INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS paid_invoices (
                    invoice_id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS subscription_checkouts (
                    account_id TEXT PRIMARY KEY, attempt TEXT NOT NULL UNIQUE);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(subscriptions)')}
            for name in ('paid_start', 'paid_through'):
                if name not in columns:
                    db.execute('ALTER TABLE subscriptions ADD COLUMN ' + name + ' REAL')

    @staticmethod
    def month_after(anchor, months):
        value = datetime.fromtimestamp(anchor, timezone.utc)
        total = value.year * 12 + value.month - 1 + months
        year, month = divmod(total, 12)
        month += 1
        return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1])).timestamp()

    def refresh(self, account_id, stamp=None):
        """Materialize only the current paid month; missed months never accumulate."""
        stamp = time.time() if stamp is None else stamp
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM subscriptions WHERE account_id=?', (account_id,)).fetchone()
            if not row or row['tier'] not in TIERS or row['status'] not in ('active', 'past_due') or not row['paid_start'] or not row['paid_through'] or not row['paid_start'] <= stamp < row['paid_through']:
                return
            index = 0
            while self.month_after(row['paid_start'], index + 1) <= stamp:
                index += 1
            start = self.month_after(row['paid_start'], index)
            end = min(self.month_after(row['paid_start'], index + 1), row['paid_through'])
            key = 'subscription:' + row['subscription_id'] + ':' + str(int(start))
            db.execute('INSERT OR IGNORE INTO credit_buckets(account_id,grant_key,kind,credits,expires,created) VALUES(?,?,?,?,?,?)',
                       (account_id, key, 'subscription', TIERS[row['tier']]['credits'], end, stamp))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def grant(self, account_id, grant_key, credits, kind, expires=None):
        if type(credits) is not int or not 0 < credits <= 100000 or kind not in ('free', 'subscription', 'wallet', 'refund'):
            raise ValueError('Invalid credit grant.')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO credit_buckets(account_id,grant_key,kind,credits,expires,created) VALUES(?,?,?,?,?,?)',
                       (account_id, grant_key, kind, credits, expires, time.time()))

    def balance(self, account_id):
        self.refresh(account_id)
        with self.connect() as db:
            db.execute('BEGIN')
            credits = db.execute('SELECT COALESCE(SUM(credits),0) FROM credit_buckets WHERE account_id=? AND (expires IS NULL OR expires>?)', (account_id, time.time())).fetchone()[0]
            reserved = db.execute("SELECT COUNT(*) FROM credit_reservations r JOIN credit_buckets b ON b.id=r.bucket_id WHERE r.account_id=? AND r.state='reserved' AND (b.expires IS NULL OR b.expires>?)", (account_id, time.time())).fetchone()[0]
        return {'available': max(0, credits - reserved), 'reserved': reserved}

    def reserve(self, account_id, reference, day, daily_cap, owner=False):
        self.refresh(account_id)
        if type(daily_cap) is not int or not 1 <= daily_cap <= 100:
            raise ValueError('Daily cap must be between 1 and 100.')
        if owner and account_id != 'owner':
            raise ValueError('Owner bypass requires the server owner identity.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM credit_reservations WHERE reference=?', (reference,)).fetchone()
            if old:
                if old['account_id'] != account_id:
                    raise ValueError('Reservation belongs to another account.')
                if old['state'] in ('reserved', 'charged'):
                    return
                raise ValueError('This reservation was already released; use an explicitly new application attempt.')
            used = db.execute("SELECT COUNT(*) FROM credit_reservations WHERE account_id=? AND day=? AND state IN ('reserved','charged')", (account_id, day)).fetchone()[0]
            if used >= daily_cap:
                raise ValueError('Your shared daily application cap has been reached.')
            bucket_id = None
            if not owner:
                row = db.execute("""SELECT b.id FROM credit_buckets b WHERE b.account_id=? AND (b.expires IS NULL OR b.expires>?)
                    AND b.credits>(SELECT COUNT(*) FROM credit_reservations r WHERE r.bucket_id=b.id AND r.state='reserved')
                    ORDER BY b.expires IS NULL,b.expires,b.id LIMIT 1""", (account_id, time.time())).fetchone()
                if not row:
                    raise ValueError('No available application credits.')
                bucket_id = row['id']
            db.execute('INSERT INTO credit_reservations VALUES(?,?,?,?,?,?,?)', (reference, account_id, bucket_id, day, 'reserved', time.time(), time.time()))

    def settle(self, account_id, reference, success):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM credit_reservations WHERE reference=? AND account_id=?', (reference, account_id)).fetchone()
            if not row:
                raise ValueError('Reservation not found for this account.')
            if row['state'] != 'reserved':
                return
            if success and row['bucket_id'] is not None:
                db.execute('UPDATE credit_buckets SET credits=credits-1 WHERE id=?', (row['bucket_id'],))
            db.execute('UPDATE credit_reservations SET state=?,updated=? WHERE reference=?', ('charged' if success else 'released', time.time(), reference))

    def refund(self, account_id, reference):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM credit_reservations WHERE reference=? AND account_id=?', (reference, account_id)).fetchone()
            if not row:
                raise ValueError('Reservation not found for this account.')
            if row['state'] == 'charged' and row['bucket_id'] is not None:
                bucket = db.execute('SELECT expires FROM credit_buckets WHERE id=?', (row['bucket_id'],)).fetchone()
                db.execute('INSERT OR IGNORE INTO credit_buckets(account_id,grant_key,kind,credits,expires,created) VALUES(?,?,?,?,?,?)',
                           (account_id, 'refund:' + reference, 'refund', 1, bucket['expires'], time.time()))
            if row['state'] in ('charged', 'reserved'):
                db.execute("UPDATE credit_reservations SET state='refunded',updated=? WHERE reference=?", (time.time(), reference))

    def subscription(self, account_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM subscriptions WHERE account_id=?', (account_id,)).fetchone()
        return dict(row) if row else {'tier': 'free', 'status': 'active', 'cycle_end': None}

    def history(self, account_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT reference,day,state,created,updated FROM credit_reservations WHERE account_id=? ORDER BY created DESC LIMIT 100', (account_id,))]
