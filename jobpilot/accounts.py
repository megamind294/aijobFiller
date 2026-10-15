"""Verified identities and one-time challenges; roles are assigned by the server."""
import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Accounts:
    def __init__(self, home):
        self.path = Path(home) / 'accounts.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL, role TEXT NOT NULL,
                    version TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS identities (
                    provider TEXT NOT NULL, subject TEXT NOT NULL, account_id TEXT NOT NULL,
                    PRIMARY KEY(provider,subject));
                CREATE TABLE IF NOT EXISTS login_challenges (
                    id TEXT PRIMARY KEY, provider TEXT, subject TEXT, digest TEXT,
                    created REAL, expires REAL, attempts INTEGER DEFAULT 0, consumed INTEGER DEFAULT 0);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM accounts WHERE id=?', (identifier,)).fetchone()
        return dict(row) if row else None

    def identity(self, provider, subject, email='', verified=False):
        if provider not in ('google', 'email', 'phone') or not verified or not isinstance(subject, str) or not 1 <= len(subject) <= 320:
            raise ValueError('A verified supported identity is required.')
        email = email.strip().casefold()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT a.* FROM accounts a JOIN identities i ON a.id=i.account_id WHERE i.provider=? AND i.subject=?', (provider, subject)).fetchone()
            if row:
                return dict(row)
            if email and db.execute('SELECT id FROM accounts WHERE email=?', (email,)).fetchone():
                raise ValueError('An account already uses this address. Sign in with its existing provider before linking another identity.')
            identifier = secrets.token_hex(16)
            db.execute('INSERT INTO accounts VALUES(?,?,?,?,?)', (identifier, email, 'customer', secrets.token_hex(24), time.time()))
            db.execute('INSERT INTO identities VALUES(?,?,?)', (provider, subject, identifier))
        return self.get(identifier)

    def link_identity(self, account_id, provider, subject, verified=False):
        if provider not in ('google', 'email', 'phone') or not verified or not subject or not self.get(account_id):
            raise ValueError('Verified identity and an existing signed-in account are required.')
        with self.connect() as db:
            existing = db.execute('SELECT account_id FROM identities WHERE provider=? AND subject=?', (provider, subject)).fetchone()
            if existing and existing['account_id'] != account_id:
                raise ValueError('This identity is already linked to another account.')
            db.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?)', (provider, subject, account_id))

    def revoke(self, identifier):
        with self.connect() as db:
            db.execute('UPDATE accounts SET version=? WHERE id=?', (secrets.token_hex(24), identifier))

    @staticmethod
    def digest(token, code, secret):
        return hmac.new(str(secret).encode(), (token + ':' + code).encode(), hashlib.sha256).hexdigest()

    def issue_challenge(self, provider, subject, code, secret, stamp=None):
        stamp = time.time() if stamp is None else stamp
        if provider not in ('email', 'phone') or not re.fullmatch(r'\d{6}', code) or len(subject) > 320:
            raise ValueError('Invalid verification request.')
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('SELECT COUNT(*) FROM login_challenges WHERE provider=? AND subject=? AND created>?', (provider, subject, stamp - 3600)).fetchone()[0]
            if count >= 5:
                raise ValueError('Too many verification requests. Try again later.')
            db.execute('INSERT INTO login_challenges(id,provider,subject,digest,created,expires) VALUES(?,?,?,?,?,?)',
                       (token, provider, subject, self.digest(token, code, secret), stamp, stamp + 600))
        return token

    def consume_challenge(self, token, code, secret, stamp=None):
        stamp = time.time() if stamp is None else stamp
        valid = False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM login_challenges WHERE id=?', (token,)).fetchone()
            if row and not row['consumed'] and row['expires'] > stamp and row['attempts'] < 5:
                valid = hmac.compare_digest(row['digest'], self.digest(token, code, secret))
                db.execute('UPDATE login_challenges SET attempts=attempts+1,consumed=? WHERE id=?', (int(valid), token))
        if not valid:
            raise ValueError('The verification code is invalid, expired, or already used.')
        return row['provider'], row['subject']
