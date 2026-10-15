"""Bounded read-only Gmail web OAuth client; secrets stay in encrypted server files."""
import base64
import hashlib
import json
import os
import secrets
import time
from email.utils import parseaddr
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, quote
from urllib.request import Request, urlopen

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
QUERY = 'newer_than:30d -in:sent -in:trash -in:spam -category:promotions {interview assessment recruiter "application" "job offer" "not selected"}'


class MailboxUnavailable(ValueError):
    pass


def setting(config, name):
    return config.get(name) or os.environ.get(name, '')


class GmailMailbox:
    def __init__(self, home, config):
        self.home = Path(home) / 'mailbox'
        self.config = config
        self.path = self.home / 'tokens.enc'

    def configured(self):
        uri = setting(self.config, 'GOOGLE_GMAIL_REDIRECT_URI')
        parsed = urlsplit(uri)
        valid = (parsed.scheme == 'https' and parsed.hostname and parsed.path == '/inbox/callback'
                 and not parsed.query and not parsed.fragment and not parsed.username and not parsed.password)
        return bool(valid and all(setting(self.config, name) for name in
                    ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'MAILBOX_ENCRYPTION_KEY')))

    def cipher(self):
        from cryptography.fernet import Fernet
        try:
            return Fernet(setting(self.config, 'MAILBOX_ENCRYPTION_KEY').encode())
        except (ValueError, TypeError):
            raise MailboxUnavailable('Mailbox encryption is not configured') from None

    def connected(self):
        return self.path.exists()

    def save_tokens(self, tokens):
        encrypted = self.cipher().encrypt(json.dumps(tokens).encode())
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.home.is_symlink() or self.path.is_symlink():
            raise MailboxUnavailable('Invalid mailbox storage')
        temp = self.home / (secrets.token_hex(12) + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encrypted)
            os.replace(temp, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temp.unlink(missing_ok=True)

    def load_tokens(self):
        from cryptography.fernet import InvalidToken
        try:
            return json.loads(self.cipher().decrypt(self.path.read_bytes()))
        except (OSError, ValueError, InvalidToken):
            raise MailboxUnavailable('Reconnect Gmail to continue') from None

    def disconnect(self):
        # Local unlink only: users can independently revoke access in Google Account settings.
        self.path.unlink(missing_ok=True)

    def authorization_url(self, state, verifier):
        if not self.configured():
            raise MailboxUnavailable('Gmail is not configured')
        self.cipher()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': setting(self.config, 'GOOGLE_CLIENT_ID'),
            'redirect_uri': setting(self.config, 'GOOGLE_GMAIL_REDIRECT_URI'),
            'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline',
            'prompt': 'consent', 'state': state, 'code_challenge': challenge,
            'code_challenge_method': 'S256'})

    def _http(self, url, data=None, token=None):
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        payload = urlencode(data).encode() if data is not None else None
        request = Request(url, data=payload, headers=headers)
        try:
            with urlopen(request, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise MailboxUnavailable('Mailbox response exceeded the safety limit')
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('Invalid response')
            return result
        except (HTTPError, URLError, TimeoutError, ValueError):
            raise MailboxUnavailable('Gmail could not complete this request; reconnect or retry') from None

    def exchange(self, code, verifier):
        if not self.configured():
            raise MailboxUnavailable('Gmail is not configured')
        tokens = self._http('https://oauth2.googleapis.com/token', {
            'client_id': setting(self.config, 'GOOGLE_CLIENT_ID'),
            'client_secret': setting(self.config, 'GOOGLE_CLIENT_SECRET'),
            'redirect_uri': setting(self.config, 'GOOGLE_GMAIL_REDIRECT_URI'),
            'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier})
        if not tokens.get('access_token') or not tokens.get('refresh_token'):
            raise MailboxUnavailable('Google did not grant offline mailbox access; reconnect')
        if SCOPE not in tokens.get('scope', '').split():
            raise MailboxUnavailable('Read-only Gmail permission was not granted')
        profile = self._http('https://gmail.googleapis.com/gmail/v1/users/me/profile', token=tokens['access_token'])
        if not profile.get('emailAddress'):
            raise MailboxUnavailable('Could not verify the connected mailbox')
        tokens['mailbox'] = profile['emailAddress'].casefold()
        tokens['expires_at'] = time.time() + int(tokens.get('expires_in', 3600))
        self.save_tokens(tokens)

    def _access(self):
        tokens = self.load_tokens()
        if tokens.get('expires_at', 0) <= time.time() + 60:
            fresh = self._http('https://oauth2.googleapis.com/token', {
                'client_id': setting(self.config, 'GOOGLE_CLIENT_ID'),
                'client_secret': setting(self.config, 'GOOGLE_CLIENT_SECRET'),
                'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token']})
            if not fresh.get('access_token'):
                raise MailboxUnavailable('Reconnect Gmail to continue')
            tokens.update(fresh)
            tokens['expires_at'] = time.time() + int(fresh.get('expires_in', 3600))
            self.save_tokens(tokens)
        return tokens

    def candidates(self, limit=50):
        if not self.configured():
            raise MailboxUnavailable('Gmail is not configured')
        limit = min(50, max(1, int(limit)))
        tokens = self._access()
        prefix = hashlib.sha256(tokens['mailbox'].encode()).hexdigest()[:16] + ':'
        base = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
        listing = self._http(base + '?' + urlencode({'q': QUERY, 'maxResults': limit}), token=tokens['access_token'])
        messages = []
        for item in listing.get('messages', [])[:limit]:
            raw = self._http(base + '/' + quote(item['id'], safe='') + '?format=full', token=tokens['access_token'])
            messages.append(normalize_message(raw, prefix))
        return messages


def body_text(part, depth=0):
    if depth > 15 or part.get('filename'):
        return ''
    if part.get('mimeType') in ('text/plain', 'text/html'):
        data = part.get('body', {}).get('data', '')[:300000]
        try:
            # HTML is stored as text and always escaped by the reader, never rendered.
            return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', errors='replace')[:200000]
        except (ValueError, TypeError):
            return ''
    children = part.get('parts', [])[:100]
    plain = [p for p in children if p.get('mimeType') == 'text/plain']
    return '\n'.join(body_text(child, depth + 1) for child in (plain or children))[:200000]


def normalize_message(raw, prefix=''):
    payload = raw.get('payload', {})
    headers = {h['name'].casefold(): h['value'] for h in payload.get('headers', [])}
    return {'id': prefix + raw['id'], 'thread_id': prefix + raw.get('threadId', raw['id']),
            'sender': parseaddr(headers.get('from', ''))[1], 'subject': headers.get('subject', ''),
            'text': body_text(payload), 'timestamp': int(raw.get('internalDate', 0)) / 1000}
