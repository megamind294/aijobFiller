"""Opaque, hashed account grants, PKCE authorization codes and rotating tokens."""
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

SCOPES = frozenset({'profile:read', 'jobs:read', 'applications:read', 'applications:write'})


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def pkce_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()


def scopes(value):
    result = value.split() if isinstance(value, str) else value
    if not isinstance(result, (list, tuple, set, frozenset)) or not result or not all(isinstance(v, str) and v in SCOPES for v in result):
        raise ValueError('Invalid scope.')
    return sorted(set(result))


def safe_redirect(uri):
    if not isinstance(uri, str) or len(uri) > 2048 or any(ord(c) < 33 for c in uri) or '\\' in uri:
        raise ValueError('Invalid redirect URI.')
    p = urlsplit(uri)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment or p.hostname == 'localhost' or '.' not in p.hostname or p.hostname.endswith(('.localhost', '.local', '.internal')) or '*' in p.hostname:
        raise ValueError('Redirects require a public HTTPS URL without credentials or fragments.')
    _ = p.port  # Reject malformed ports before persisting client metadata.
    try:
        if not ipaddress.ip_address(p.hostname).is_global:
            raise ValueError('Private redirect addresses are not allowed.')
    except ValueError as exc:
        if 'Private' in str(exc):
            raise
    return uri


class TokenStore:
    def __init__(self, home):
        self.path = Path(home) / 'api-grants.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS api_clients(id TEXT PRIMARY KEY, metadata TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS api_codes(hash TEXT PRIMARY KEY, client_id TEXT, redirect_uri TEXT,
                account_id TEXT, account_version TEXT, scopes TEXT, resource TEXT, challenge TEXT, expires REAL, used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS api_tokens(hash TEXT PRIMARY KEY, kind TEXT, account_id TEXT,
                account_version TEXT, scopes TEXT, resource TEXT, client_id TEXT, grant_id TEXT, expires REAL, revoked INTEGER DEFAULT 0);
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

    def register_client(self, metadata):
        if not isinstance(metadata, dict):
            raise ValueError('Invalid client metadata.')
        uris = metadata.get('redirect_uris')
        if not isinstance(uris, list) or not 1 <= len(uris) <= 5:
            raise ValueError('Supply one to five redirect URIs.')
        uris = [safe_redirect(uri) for uri in uris]
        if metadata.get('token_endpoint_auth_method', 'none') != 'none' or metadata.get('grant_types', ['authorization_code', 'refresh_token']) != ['authorization_code', 'refresh_token'] or metadata.get('response_types', ['code']) != ['code']:
            raise ValueError('Only public PKCE authorization-code clients are supported.')
        name = metadata.get('client_name', 'MCP client')
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise ValueError('Invalid client name.')
        result = {'client_id': secrets.token_urlsafe(24), 'client_name': name, 'redirect_uris': uris,
                  'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code'], 'token_endpoint_auth_method': 'none'}
        with self.connect() as db:
            db.execute('INSERT INTO api_clients VALUES(?,?,?)', (result['client_id'], json.dumps(result), time.time()))
        return result

    def client(self, client_id):
        with self.connect() as db:
            row = db.execute('SELECT metadata FROM api_clients WHERE id=?', (client_id,)).fetchone()
        if not row:
            raise ValueError('Unknown OAuth client.')
        return json.loads(row['metadata'])

    def authorization_code(self, client_id, redirect_uri, account_id, account_version, allowed, resource, challenge):
        if redirect_uri not in self.client(client_id)['redirect_uris'] or not re.fullmatch(r'[A-Za-z0-9_-]{43}', challenge):
            raise ValueError('Invalid redirect or S256 challenge.')
        code = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('INSERT INTO api_codes(hash,client_id,redirect_uri,account_id,account_version,scopes,resource,challenge,expires) VALUES(?,?,?,?,?,?,?,?,?)',
                       (digest(code), client_id, redirect_uri, account_id, account_version, ' '.join(scopes(allowed)), resource, challenge, time.time() + 300))
        return code

    def _issue(self, db, account_id, version, allowed, resource, client_id='', grant_id=None, kind='access', ttl=3600):
        raw = secrets.token_urlsafe(48)
        db.execute('INSERT INTO api_tokens VALUES(?,?,?,?,?,?,?,?,?,0)',
                   (digest(raw), kind, account_id, version, ' '.join(scopes(allowed)), resource, client_id, grant_id or secrets.token_hex(16), time.time() + ttl))
        return raw

    def issue(self, account_id, version, allowed, resource, ttl=3600):
        with self.connect() as db:
            return self._issue(db, account_id, version, allowed, resource, ttl=ttl)

    def _pair(self, db, row, grant_id=None):
        grant_id = grant_id or secrets.token_hex(16)
        args = (db, row['account_id'], row['account_version'], row['scopes'].split(), row['resource'], row['client_id'], grant_id)
        return {'access_token': self._issue(*args), 'refresh_token': self._issue(*args, kind='refresh', ttl=2592000),
                'token_type': 'Bearer', 'expires_in': 3600, 'scope': row['scopes']}

    def exchange(self, code, client_id, redirect_uri, verifier, resource):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM api_codes WHERE hash=?', (digest(code),)).fetchone()
            if not row or row['used'] or row['expires'] <= time.time() or row['client_id'] != client_id or row['redirect_uri'] != redirect_uri or row['resource'] != resource or not re.fullmatch(r'[A-Za-z0-9._~-]{43,128}', verifier) or not hmac.compare_digest(row['challenge'], pkce_challenge(verifier)):
                raise ValueError('Invalid authorization grant.')
            db.execute('UPDATE api_codes SET used=1 WHERE hash=?', (digest(code),))
            return self._pair(db, row)

    def refresh(self, token, client_id, resource):
        result = None
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM api_tokens WHERE hash=? AND kind=\'refresh\'', (digest(token),)).fetchone()
            if row and row['client_id'] == client_id and row['resource'] == resource:
                if row['revoked']:
                    # A reused rotating refresh token invalidates the entire grant.
                    db.execute('UPDATE api_tokens SET revoked=1 WHERE grant_id=?', (row['grant_id'],))
                elif row['expires'] > time.time():
                    db.execute('UPDATE api_tokens SET revoked=1 WHERE hash=?', (digest(token),))
                    result = self._pair(db, row, row['grant_id'])
        if result is None:
            raise ValueError('Invalid refresh grant.')
        return result

    def verify(self, token, required, resource):
        if not isinstance(token, str) or len(token) > 256:
            return None
        with self.connect() as db:
            row = db.execute('SELECT * FROM api_tokens WHERE hash=? AND kind=\'access\'', (digest(token),)).fetchone()
        if not row or row['revoked'] or row['expires'] <= time.time() or row['resource'] != resource or (required and required not in row['scopes'].split()):
            return None
        return dict(row)

    def revoke(self, token, client_id=None):
        with self.connect() as db:
            row = db.execute('SELECT grant_id,client_id FROM api_tokens WHERE hash=?', (digest(token),)).fetchone()
            if row and (client_id is None or row['client_id'] == client_id):
                db.execute('UPDATE api_tokens SET revoked=1 WHERE grant_id=?', (row['grant_id'],))

    def connections(self, account_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT grant_id,client_id,scopes,MAX(expires) AS expires FROM api_tokens WHERE account_id=? AND revoked=0 AND expires>? GROUP BY grant_id,client_id,scopes', (account_id, time.time()))]

    def revoke_grant(self, account_id, grant_id):
        with self.connect() as db:
            db.execute('UPDATE api_tokens SET revoked=1 WHERE account_id=? AND grant_id=?', (account_id, grant_id))
