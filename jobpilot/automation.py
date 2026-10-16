"""Explicitly activated, account-scoped application lanes and shared limits."""
import json
import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .applications import Applications, fingerprint, job_facts
from .core import now, origin, read_json, validate_profile
from .worker import TaskQueue


class Automation:
    def __init__(self, store, ledger, account_id, *, entitled=False, owner=False, adapters=None):
        self.store, self.ledger, self.account_id = store, ledger, account_id
        self.entitled, self.owner, self.adapters = entitled, owner, adapters or {}
        if owner and account_id != 'owner':
            raise ValueError('Owner identity required.')
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS automation_lanes (id TEXT PRIMARY KEY,config TEXT,active INTEGER DEFAULT 0,updated TEXT);
                CREATE TABLE IF NOT EXISTS automation_settings (id INTEGER PRIMARY KEY CHECK(id=1),daily_cap INTEGER,timezone TEXT);
                INSERT OR IGNORE INTO automation_settings VALUES(1,10,'UTC');
                CREATE TABLE IF NOT EXISTS automation_history (id INTEGER PRIMARY KEY,created TEXT,lane_id TEXT,job_id TEXT,outcome TEXT,detail TEXT);
                CREATE TABLE IF NOT EXISTS automation_applications (application_id TEXT PRIMARY KEY,lane_id TEXT,config_hash TEXT,
                    submission_mode TEXT NOT NULL DEFAULT 'automatic');''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(automation_applications)')}
            if 'submission_mode' not in columns:
                # Older packages have no reliable manual/automatic provenance. Keep
                # their existing standing-authorization checks rather than bypassing them.
                db.execute("ALTER TABLE automation_applications ADD COLUMN submission_mode TEXT NOT NULL DEFAULT 'automatic'")

    def settings(self, daily_cap=None, timezone_name=None):
        with self.store.connect() as db:
            old = dict(db.execute('SELECT daily_cap,timezone FROM automation_settings WHERE id=1').fetchone())
            if daily_cap is None and timezone_name is None:
                return old
            cap = old['daily_cap'] if daily_cap is None else daily_cap
            zone = old['timezone'] if timezone_name is None else timezone_name
            if type(cap) is not int or not 1 <= cap <= 100:
                raise ValueError('Shared daily cap must be between 1 and 100.')
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, TypeError, ValueError):
                raise ValueError('Choose an IANA timezone such as Europe/Warsaw.') from None
            db.execute('UPDATE automation_settings SET daily_cap=?,timezone=? WHERE id=1', (cap, zone))
            return {'daily_cap': cap, 'timezone': zone}

    def lanes(self):
        with self.store.connect() as db:
            return [{**json.loads(row['config']), 'id': row['id'], 'active': bool(row['active']), 'updated': row['updated']}
                    for row in db.execute('SELECT * FROM automation_lanes ORDER BY rowid')]

    def save_lane(self, value, identifier=None):
        if not isinstance(value, dict):
            raise ValueError('Invalid lane configuration.')
        clean = {'name': str(value.get('name', '')).strip(), 'mode': value.get('mode', 'review-first'),
                 'optimization': value.get('optimization', 'honest'), 'cover_letter': value.get('cover_letter', True),
                 'resume_profile': value.get('resume_profile', 'default'), 'threshold': value.get('threshold', 0)}
        if not clean['name'] or len(clean['name']) > 100 or clean['mode'] not in ('review-first', 'automatic'):
            raise ValueError('Enter a name and a supported submission mode.')
        if clean['optimization'] not in ('off', 'honest'):
            raise ValueError('Aggressive optimization requires a configured AI provider and is unavailable.')
        self.profile(clean['resume_profile'])
        if type(clean['threshold']) is not int or not 0 <= clean['threshold'] <= 100 or type(clean['cover_letter']) is not bool:
            raise ValueError('Invalid threshold or cover-letter rule.')
        for name in ('keywords', 'locations', 'exclusions'):
            items = value.get(name, [])
            if not isinstance(items, list) or len(items) > 30 or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in items):
                raise ValueError('Use at most 30 short values for ' + name + '.')
            clean[name] = [x.strip() for x in items]
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if identifier:
                if not db.execute('SELECT 1 FROM automation_lanes WHERE id=?', (identifier,)).fetchone():
                    raise ValueError('Lane not found.')
                # Any rule change revokes standing authorization until explicitly activated again.
                db.execute('UPDATE automation_lanes SET config=?,active=0,updated=? WHERE id=?', (json.dumps(clean), now(), identifier))
            else:
                if db.execute('SELECT COUNT(*) FROM automation_lanes').fetchone()[0] >= 5:
                    raise ValueError('A maximum of five lanes is supported.')
                identifier = secrets.token_hex(16)
                db.execute('INSERT INTO automation_lanes VALUES(?,?,0,?)', (identifier, json.dumps(clean), now()))
        return next(lane for lane in self.lanes() if lane['id'] == identifier)

    def activate(self, identifier, active):
        if type(active) is not bool:
            raise ValueError('Invalid activation.')
        if active and not (self.owner or self.entitled):
            raise ValueError('Auto Apply requires the Power plan.')
        with self.store.connect() as db:
            if not db.execute('UPDATE automation_lanes SET active=?,updated=? WHERE id=?', (int(active), now(), identifier)).rowcount:
                raise ValueError('Lane not found.')

    def preview(self, instant=None):
        instant = instant or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise ValueError('Preview time must include a timezone.')
        settings = self.settings()
        candidates = []
        for lane in self.lanes():
            profile = self.profile(lane['resume_profile'])
            for job in self.store.rows():
                text = ' '.join(str(job.get(key) or '') for key in ('title', 'company', 'description', 'location')).casefold()
                skills = profile.get('skills', [])
                score = round(100 * sum(skill.casefold() in text for skill in skills) / max(1, len(skills)))
                reason = ''
                if any(item.casefold() in text for item in lane['exclusions']):
                    reason = 'Standing exclusion'
                elif lane['keywords'] and not any(item.casefold() in text for item in lane['keywords']):
                    reason = 'Keyword filter'
                elif lane['locations'] and not any(item.casefold() in (job.get('location') or '').casefold() for item in lane['locations']):
                    reason = 'Location filter'
                elif score < lane['threshold']:
                    reason = 'Below skill coverage threshold'
                elif not self.store.can_apply(job['id']):
                    reason = 'Application already active, sent, uncertain or skipped'
                elif lane['mode'] == 'automatic' and origin(job['url']) not in self.adapters:
                    reason = 'Automatic submission requires a supported adapter'
                with self.store.connect() as db:
                    existing = db.execute("SELECT * FROM application_runs WHERE job_id=? AND state NOT IN ('SUPERSEDED')", (job['id'],)).fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE name='application_runs'").fetchone() else []
                retry = len(existing) == 1 and self._budget_retry(existing[0], lane, profile)
                if existing and not retry and not reason:
                    reason = 'Existing application package; review its history'
                candidates.append({'lane_id': lane['id'], 'lane_name': lane['name'], 'job_id': job['id'], 'title': job['title'],
                                   'company': job['company'], 'score': score, 'reason': reason, 'eligible': not bool(reason), 'active': lane['active']})
        day = instant.astimezone(ZoneInfo(settings['timezone'])).date().isoformat()
        return {**settings, 'day': day, 'candidates': candidates, 'entitled': self.owner or self.entitled,
                'next_run': None, 'schedule': 'On demand; external scheduler must invoke the account worker explicitly.'}

    def _budget_retry(self, run, lane, profile):
        """Only untouched, current packages without any credit reservation can retry."""
        if run['state'] != 'DOCUMENT_REVIEW' or run['document_approved'] or run['form_approved']:
            return False
        source = json.loads(run['snapshot'])
        if (not run['request_key'].startswith('auto:') or source['profile'] != profile or
                source.get('profile_id', 'default') != lane['resume_profile'] or
                source['optimization'] != lane['optimization'] or bool(run['letter_path']) != lane['cover_letter']):
            return False
        with self.store.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='application_tasks'").fetchone() and db.execute(
                    'SELECT 1 FROM application_tasks WHERE application_id=?', (run['id'],)).fetchone():
                return False
        with self.ledger.connect() as db:
            if db.execute('SELECT 1 FROM credit_reservations WHERE reference=?', ('application:' + run['id'],)).fetchone():
                return False
        try:
            Applications(self.store).assert_current(dict(run))
        except (ValueError, FileNotFoundError):
            return False
        return True

    def run(self):
        if not (self.owner or self.entitled):
            raise ValueError('Auto Apply requires the Power plan.')
        preview = self.preview()
        created = []
        apps, queue = Applications(self.store), TaskQueue(self.store)
        lanes = {lane['id']: lane for lane in self.lanes()}
        seen = set()
        for candidate in preview['candidates']:
            lane = lanes[candidate['lane_id']]
            job_id = candidate['job_id']
            if not lane['active'] or not candidate['eligible'] or job_id in seen:
                continue
            seen.add(job_id)
            try:
                profile = self.profile(lane['resume_profile'])
                request_key = 'auto:' + fingerprint({'job': job_id, 'profile': profile, 'profile_id': lane['resume_profile'], 'job_facts': job_facts(self.store.get(job_id))})
                run = apps.prepare(job_id, profile, request_key, lane['cover_letter'], lane['optimization'], profile_id=lane['resume_profile'])
                self.ledger.reserve(self.account_id, 'application:' + run['id'], preview['day'], preview['daily_cap'], owner=self.owner)
                with self.store.connect() as db:
                    db.execute('INSERT OR IGNORE INTO automation_applications(application_id,lane_id,config_hash,submission_mode) VALUES(?,?,?,?)',
                               (run['id'], lane['id'], fingerprint(lane), lane['mode']))
                if lane['mode'] == 'automatic':
                    apps.approve_documents(run['id'], run['document_hash'])
                    queue.enqueue(run['id'], 'fill')
                created.append(run['id'])
                outcome, detail = 'prepared', run['id']
            except ValueError as exc:
                outcome, detail = 'paused', str(exc)
            with self.store.connect() as db:
                db.execute('INSERT INTO automation_history(created,lane_id,job_id,outcome,detail) VALUES(?,?,?,?,?)', (now(), lane['id'], job_id, outcome, detail))
        return created

    def authorized_automatic(self, application_id):
        if not (self.owner or self.entitled):
            return False
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM automation_applications WHERE application_id=?', (application_id,)).fetchone()
        lane = next((lane for lane in self.lanes() if row and lane['id'] == row['lane_id']), None)
        return bool(lane and lane['active'] and lane['mode'] == 'automatic' and fingerprint(lane) == row['config_hash'])

    def profile(self, identifier):
        if identifier == 'default':
            profile = read_json(self.store.home / 'profile.json')
        else:
            from .profile_vault import ProfileVault
            profile = ProfileVault(self.store.home).get(identifier)['profile']
        return validate_profile(profile)

    def history(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM automation_history ORDER BY id DESC LIMIT 100')]
