"""Private, deterministic mailbox evidence and local drafts. No sending API."""
import json
import re
from datetime import datetime, timezone


def phrase(value, text):
    return bool(value and re.search(r'(?<!\w)' + re.escape(value.casefold()) + r'(?!\w)', text.casefold()))


def routing_evidence(message, jobs):
    text = message['subject'] + '\n' + message['text']
    urls = {url.rstrip('.,);!?') for url in re.findall(r'https?://[^\s<>"\x27]+', text)}
    exact = [j for j in jobs if phrase(j['id'], text) or
             j['url'] in urls]
    if len(exact) == 1:
        return exact[0]['id'], 'Exact application identifier or job URL', [exact[0]['id']]
    if exact:
        return None, 'Conflicting application identifiers; review required', [j['id'] for j in exact]
    companies = [j for j in jobs if phrase(j['company'], text)]
    roles = [j for j in companies if phrase(j['title'], text)]
    if len(roles) == 1:
        return roles[0]['id'], 'Exact company and role title', [roles[0]['id']]
    return None, 'No unique company and role evidence; review required', [j['id'] for j in companies]


def category(message):
    text = (message['subject'] + '\n' + message['text']).casefold()
    # Suggestions require explicit outcome language; acknowledgement never proves submission.
    rules = [('REJECTED', r'\b(not moving forward with your application|not selected|application was unsuccessful)\b'),
             ('OFFER', r'\b(offer of employment|job offer|pleased to offer you)\b'),
             ('INTERVIEW', r'\b(interview invitation|invite you to interview|schedule an interview)\b')]
    matches = [status for status, pattern in rules if re.search(pattern, text)]
    return matches[0] if len(matches) == 1 else 'REVIEW'


class Inbox:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS inbox_messages (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, subject TEXT NOT NULL,
                    sender TEXT NOT NULL, text TEXT NOT NULL, timestamp REAL NOT NULL,
                    job_id TEXT, routing TEXT NOT NULL, reason TEXT NOT NULL,
                    candidates TEXT NOT NULL, category TEXT NOT NULL, draft TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS inbox_outcomes (
                    job_id TEXT PRIMARY KEY, timestamp REAL NOT NULL, status TEXT NOT NULL,
                    job_updated TEXT NOT NULL, message_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inbox_oauth_states (
                    digest TEXT PRIMARY KEY, expires REAL NOT NULL);
            ''')

    def ingest(self, messages):
        count = 0
        for msg in sorted(messages, key=lambda m: float(m.get('timestamp', 0))):
            if not msg.get('id') or len(str(msg['id'])) > 200:
                continue
            message = {key: str(msg.get(key, ''))[:limit] for key, limit in
                       [('id', 200), ('thread_id', 200), ('subject', 2000), ('sender', 1000), ('text', 200000)]}
            message['thread_id'] = message['thread_id'] or message['id']
            message['timestamp'] = max(0, float(msg.get('timestamp', 0)))
            job, reason, candidates = routing_evidence(message, self.store.rows())
            status = category(message)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                added = db.execute('''INSERT OR IGNORE INTO inbox_messages
                    (id,thread_id,subject,sender,text,timestamp,job_id,routing,reason,candidates,category)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                    tuple(message[k] for k in ('id', 'thread_id', 'subject', 'sender', 'text', 'timestamp')) +
                    (job, 'automatic' if job else 'review', reason, json.dumps(candidates), status)).rowcount
                if added:
                    count += 1
                    if job and status != 'REVIEW':
                        self._outcome(db, job, status, message)
        return count

    def _outcome(self, db, job_id, status, message):
        job = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        prior = db.execute('SELECT * FROM inbox_outcomes WHERE job_id=?', (job_id,)).fetchone()
        # A later manual action or an existing terminal decision always wins.
        if job['status'] in {'OFFER', 'REJECTED', 'SKIPPED'}:
            return
        if prior and (message['timestamp'] <= prior['timestamp'] or job['updated'] != prior['job_updated']):
            return
        if message['timestamp'] <= datetime.fromisoformat(job['updated']).timestamp():
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec='microseconds')
        note = 'Mailbox evidence: ' + message['id'] + ' (review/correct in Inbox)'
        db.execute('UPDATE jobs SET status=?, updated=?, note=? WHERE id=?', (status, stamp, note, job_id))
        db.execute('INSERT INTO events(job_id,created,status,note) VALUES(?,?,?,?)', (job_id, stamp, status, note))
        db.execute('INSERT OR REPLACE INTO inbox_outcomes VALUES(?,?,?,?,?)',
                   (job_id, message['timestamp'], status, stamp, message['id']))

    def rows(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM inbox_messages ORDER BY timestamp DESC,id')]

    def get(self, message_id):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM inbox_messages WHERE id=?', (message_id,)).fetchone()
        if row is None:
            raise ValueError('Message not found')
        return dict(row)

    def thread(self, message_id):
        row = self.get(message_id)
        return [m for m in reversed(self.rows()) if m['thread_id'] == row['thread_id']]

    def route(self, message_id, job_id):
        self.get(message_id)
        if job_id:
            self.store.get(job_id)
        with self.store.connect() as db:
            # Relinking records a correction without replaying status effects on another application.
            db.execute("UPDATE inbox_messages SET job_id=?,routing='manual',reason='User corrected routing' WHERE id=?",
                       (job_id or None, message_id))

    def save_draft(self, message_id, text):
        self.get(message_id)
        if len(text) > 20000:
            raise ValueError('Draft exceeds 20,000 characters')
        with self.store.connect() as db:
            db.execute('UPDATE inbox_messages SET draft=? WHERE id=?', (text, message_id))
