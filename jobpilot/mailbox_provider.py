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
from urllib.parse import urlencode, urlsplit, quote, parse_qs
from urllib.request import Request, urlopen

SCOPE = 'https://www.googleapis.com/auth/gmail.readonly'
QUERY = 'newer_than:30d -in:sent -in:trash -in:spam -category:promotions {interview assessment recruiter "application" "job offer" "not selected"}'


class MailboxUnavailable(ValueError):
    pass


class MailboxCursorExpired(MailboxUnavailable):
    pass


class MailboxMessageGone(MailboxUnavailable):
    pass


def setting(config, name):
    return config.get(name) or os.environ.get(name, '')


class GmailMailbox:
    def __init__(self, home, config):
        self.home = Path(home) / 'mailbox'
        self.config = config
        self.path = self.home / 'tokens.enc'
        self._pending_page_id = None

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
        except HTTPError as error:
            # Gmail may invalidate a continuation token as the mailbox changes.
            # A failure fetching a message must not discard a valid list cursor.
            if error.code == 400 and 'pageToken' in parse_qs(urlsplit(url).query):
                raise MailboxCursorExpired('Gmail pagination expired; retry sync to start a fresh scan') from None
            if error.code == 404 and '/messages/' in urlsplit(url).path:
                raise MailboxMessageGone('This Gmail message is no longer available') from None
            raise MailboxUnavailable('Gmail could not complete this request; reconnect or retry') from None
        except (URLError, TimeoutError, ValueError):
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
        """Read one bounded page per user sync, continuing across provider instances.

        Store a pending continuation alongside encrypted account tokens. The caller
        must acknowledge successful ingestion before the committed cursor moves.
        Failed source reads or ingestion retry the same bounded page.
        """
        if not self.configured():
            raise MailboxUnavailable('Gmail is not configured')
        limit = min(50, max(1, int(limit)))
        self._pending_page_id = None
        tokens = self._access()
        prefix = hashlib.sha256(tokens['mailbox'].encode()).hexdigest()[:16] + ':'
        base = 'https://gmail.googleapis.com/gmail/v1/users/me/messages'
        fields = {'q': QUERY, 'maxResults': limit}
        page_token = self._cursor_page(tokens)
        if page_token:
            fields['pageToken'] = page_token
        pending = tokens.get('sync_pending', {})
        if (isinstance(pending, dict) and pending.get('mailbox') == tokens['mailbox']
                and pending.get('query') == QUERY and pending.get('page_token') == page_token
                and isinstance(pending.get('message_ids'), list)):
            ids = pending['message_ids']
            next_page = pending.get('next_page')
        else:
            try:
                listing = self._http(base + '?' + urlencode(fields), token=tokens['access_token'])
            except MailboxCursorExpired:
                latest = self._current_connection(tokens)
                if self._cursor_page(latest) == page_token:
                    latest.pop('sync_cursor', None)
                    latest.pop('sync_pending', None)
                    self.save_tokens(latest)
                raise
            ids = [item['id'] for item in listing.get('messages', [])[:limit]]
            next_page = listing.get('nextPageToken')
        if next_page:
            if not isinstance(next_page, str):
                raise MailboxUnavailable('Invalid Gmail pagination response; retry sync')
        latest = self._current_connection(tokens)
        if self._cursor_page(latest) != page_token:
            raise MailboxUnavailable('Mailbox sync progressed in another request; retry sync')
        pending_id = secrets.token_hex(16)
        latest['sync_pending'] = {'mailbox': tokens['mailbox'], 'query': QUERY,
                                  'page_token': page_token, 'next_page': next_page, 'attempt': pending_id,
                                  'message_ids': ids, 'fetched_count': min(limit, len(ids))}
        self.save_tokens(latest)
        messages = []
        for message_id in ids[:limit]:
            try:
                raw = self._http(base + '/' + quote(message_id, safe='') + '?format=full', token=tokens['access_token'])
            except MailboxMessageGone:
                continue
            messages.append(normalize_message(raw, prefix))
        self._pending_page_id = pending_id
        return messages

    @staticmethod
    def _cursor_page(tokens):
        cursor = tokens.get('sync_cursor', {})
        if (isinstance(cursor, dict) and cursor.get('mailbox') == tokens.get('mailbox')
                and cursor.get('query') == QUERY):
            return cursor.get('page_token') or None
        return None

    def _current_connection(self, original):
        # Re-read before updating metadata so a concurrent refresh is preserved,
        # and a disconnect/reconnect cannot be overwritten by an older scan.
        tokens = self.load_tokens()
        if (tokens.get('mailbox') != original['mailbox']
                or tokens.get('refresh_token') != original.get('refresh_token')):
            raise MailboxUnavailable('Mailbox connection changed; retry sync')
        return tokens

    def acknowledge_candidates(self):
        """Commit the pending page only after the caller has ingested its messages."""
        tokens = self.load_tokens()
        pending = tokens.get('sync_pending', {})
        if (not isinstance(pending, dict) or pending.get('mailbox') != tokens.get('mailbox')
                or pending.get('query') != QUERY
                or not self._pending_page_id or pending.get('attempt') != self._pending_page_id
                or pending.get('page_token') != self._cursor_page(tokens)):
            raise MailboxUnavailable('Mailbox sync changed; retry sync before acknowledging')
        next_page = pending.get('next_page')
        remaining = pending.get('message_ids', [])[pending.get('fetched_count', 0):]
        if remaining:
            pending['message_ids'] = remaining
            pending['fetched_count'] = 0
            pending.pop('attempt', None)
            self.save_tokens(tokens)
            self._pending_page_id = None
            return
        if next_page:
            tokens['sync_cursor'] = {'mailbox': tokens['mailbox'], 'query': QUERY, 'page_token': next_page}
        else:
            tokens.pop('sync_cursor', None)
        tokens.pop('sync_pending', None)
        self.save_tokens(tokens)
        self._pending_page_id = None


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
