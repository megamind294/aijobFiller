"""Versioned application packages and separate document/form approvals."""
import hashlib
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from .core import now, read_json, validate_profile, write_json
from .resume import render_pdf, tailor


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def job_facts(job):
    return {key: job.get(key, '') for key in ('url', 'title', 'company', 'description', 'location')}


class Applications:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS application_runs (
                id TEXT PRIMARY KEY, job_id TEXT NOT NULL, request_key TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
                profile_hash TEXT, job_hash TEXT, document_hash TEXT, snapshot TEXT,
                resume_path TEXT, letter_path TEXT, document_approved INTEGER DEFAULT 0,
                form_approved INTEGER DEFAULT 0, form_hash TEXT DEFAULT '', form TEXT DEFAULT '{}',
                receipt TEXT DEFAULT '{}', error TEXT DEFAULT '', manual_evidence TEXT NOT NULL DEFAULT '{}')''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(application_runs)')}
            if 'manual_evidence' not in columns:
                db.execute("ALTER TABLE application_runs ADD COLUMN manual_evidence TEXT NOT NULL DEFAULT '{}'")

    def get(self, identifier):
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM application_runs WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise ValueError('Application package not found.')
        return dict(row)

    def rows(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM application_runs ORDER BY created DESC LIMIT 500')]

    def snapshot(self, identifier):
        return json.loads(self.get(identifier)['snapshot'])

    def prepare(self, job_id, profile, request_key, cover_letter=True, optimization='honest', *, letter_text_override=None, parent_id=None, profile_id='default', ai_consent=False):
        if optimization == 'ai':
            from .service import ProcessLock
            try:
                with ProcessLock(self.store.home / 'ai-preparation.lock'):
                    return self._prepare(job_id, profile, request_key, cover_letter, optimization,
                                         letter_text_override=letter_text_override, parent_id=parent_id,
                                         profile_id=profile_id, ai_consent=ai_consent)
            except RuntimeError as exc:
                if 'already holds' in str(exc):
                    raise ValueError('Another AI document request is running. Please try again when it finishes.') from None
                raise
        return self._prepare(job_id, profile, request_key, cover_letter, optimization,
                             letter_text_override=letter_text_override, parent_id=parent_id, profile_id=profile_id)

    def _prepare(self, job_id, profile, request_key, cover_letter=True, optimization='honest', *, letter_text_override=None, parent_id=None, profile_id='default', ai_consent=False):
        validate_profile(profile)
        if optimization not in ('off', 'honest', 'ai'):
            raise ValueError('Choose factual preparation, original ordering or an explicitly reviewed AI rewrite.')
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
            raise ValueError('Invalid preparation request identifier.')
        job = self.store.get(job_id)
        if not job.get('description', '').strip():
            raise ValueError('Add the job description before preparing an application.')
        ai_prepared, ai_provenance = None, []
        if optimization == 'ai':
            # Never keep the SQLite write lock while waiting on a remote provider.
            with self.store.connect() as db:
                old = db.execute('SELECT * FROM application_runs WHERE request_key=?', (request_key,)).fetchone()
                if old:
                    if old['job_id'] != job_id:
                        raise ValueError('This preparation request already belongs to another job.')
                    return dict(old)
                blocked = db.execute("SELECT 1 FROM application_runs WHERE job_id=? AND state IN ('SUBMITTING','SUBMISSION_UNCONFIRMED','SUBMITTED')", (job_id,)).fetchone()
            if blocked or job['status'] in ('SUBMITTING','SUBMISSION_UNCONFIRMED','SUBMITTED','CONFIRMED','INTERVIEW','REJECTED','OFFER'):
                raise ValueError('Submission already recorded or uncertain. Check history before any new attempt.')
            if parent_id:
                parent = self.get(parent_id)
                self.assert_current(parent)
                previous = json.loads(parent['snapshot'])
                if parent['job_id'] != job_id or previous['optimization'] != 'ai' or previous['profile'] != profile:
                    raise ValueError('The previous AI document version does not match this application.')
                ai_prepared, ai_provenance = previous['prepared_profile'], previous['ai_provenance']
            else:
                if ai_consent is not True:
                    raise ValueError('Confirm sending resume and job text to Google Gemini before requesting AI preparation.')
                from .ai_preparation import prepare_profile
                ai_prepared, ai_provenance = prepare_profile(profile, job)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM application_runs WHERE request_key=?', (request_key,)).fetchone()
            if old:
                if old['job_id'] != job_id:
                    raise ValueError('This preparation request already belongs to another job.')
                return dict(old)
            blocked = db.execute("SELECT id FROM application_runs WHERE job_id=? AND state IN ('SUBMITTING','SUBMISSION_UNCONFIRMED','SUBMITTED')", (job_id,)).fetchone()
            if blocked or job['status'] in ('SUBMITTING','SUBMISSION_UNCONFIRMED','SUBMITTED','CONFIRMED','INTERVIEW','REJECTED','OFFER'):
                raise ValueError('Submission already recorded or uncertain. Check history before any new attempt.')
            identifier = secrets.token_hex(16)
            prepared = ai_prepared if optimization == 'ai' else tailor(profile, job, use_ai=False) if optimization == 'honest' else profile
            directory = self.store.home / 'documents' / identifier
            directory.mkdir(parents=True, mode=0o700)
            resume = directory / 'resume.pdf'
            render_pdf(prepared, resume)
            letter_text = ''
            letter_path = ''
            if cover_letter:
                base = profile.get('custom', {}).get('cover_letter', '').strip()
                letter_text = letter_text_override if letter_text_override is not None else base or ('Dear hiring team,\n\nI am applying for ' + job['title'] + ' at ' + job['company'] + '.\n\n' + profile.get('summary', '') + '\n\nThank you for considering my application.\n' + profile['personal']['first_name'] + ' ' + profile['personal']['last_name'])
                from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
                from reportlab.lib.styles import getSampleStyleSheet
                from xml.sax.saxutils import escape
                letter_path = str(directory / 'cover-letter.pdf')
                story = []
                for line in letter_text.splitlines():
                    story.append(Paragraph(escape(line), getSampleStyleSheet()['BodyText']) if line else Spacer(1, 12))
                SimpleDocTemplate(letter_path).build(story)
            snapshot = {'profile': profile, 'job': job_facts(job), 'prepared_profile': prepared,
                        'cover_letter': letter_text, 'optimization': optimization, 'created': now(), 'parent_id': parent_id,
                        'letter_provenance': 'user-edited' if letter_text_override is not None else 'verified-source', 'profile_id': profile_id}
            if optimization == 'ai':
                snapshot.update(ai_provider='Google Gemini', ai_provenance=ai_provenance, ai_review_required=True)
            write_json(directory / 'source.json', snapshot)
            document_hash = fingerprint({'resume': hashlib.sha256(resume.read_bytes()).hexdigest(),
                                         'letter': hashlib.sha256(Path(letter_path).read_bytes()).hexdigest() if letter_path else ''})
            stamp = now()
            db.execute('''INSERT INTO application_runs(id,job_id,request_key,state,created,updated,profile_hash,job_hash,document_hash,snapshot,resume_path,letter_path)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (identifier, job_id, request_key, 'DOCUMENT_REVIEW', stamp, stamp,
                fingerprint(profile), fingerprint(job_facts(job)), document_hash, json.dumps(snapshot, ensure_ascii=False), str(resume), letter_path))
        self.store.update(job_id, 'NEEDS_REVIEW', note='Prepared versioned application package; review documents.', resume=resume)
        return self.get(identifier)

    def assert_current(self, run):
        profile_id = json.loads(run['snapshot']).get('profile_id', 'default')
        if profile_id == 'default':
            profile = read_json(self.store.home / 'profile.json')
        else:
            from .profile_vault import ProfileVault
            profile = ProfileVault(self.store.home).get(profile_id)['profile']
        if fingerprint(profile) != run['profile_hash'] or fingerprint(job_facts(self.store.get(run['job_id']))) != run['job_hash']:
            raise ValueError('Source facts or the posting changed. Prepare a new package before approval or submission.')
        self.verify_artifacts(run)

    def artifact(self, identifier, kind):
        if kind not in ('resume', 'letter', 'source'):
            raise ValueError('Unknown artifact.')
        run = self.get(identifier)
        directory = self.store.home / 'documents' / identifier
        path = directory / 'source.json' if kind == 'source' else Path(run[kind + '_path'])
        expected = directory / {'resume': 'resume.pdf', 'letter': 'cover-letter.pdf', 'source': 'source.json'}[kind]
        if path != expected or any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
            raise ValueError('Artifact path is unavailable or unsafe.')
        return path

    def verify_artifacts(self, run):
        resume = self.artifact(run['id'], 'resume')
        letter = self.artifact(run['id'], 'letter') if run['letter_path'] else None
        digest = fingerprint({'resume': hashlib.sha256(resume.read_bytes()).hexdigest(),
                              'letter': hashlib.sha256(letter.read_bytes()).hexdigest() if letter else ''})
        if digest != run['document_hash'] or read_json(self.artifact(run['id'], 'source')) != json.loads(run['snapshot']):
            raise ValueError('The immutable application artifacts changed. Prepare a new package.')

    def edit_letter(self, identifier, text, request_key):
        run = self.get(identifier)
        self.assert_current(run)
        if run['state'] in ('SUBMITTING', 'SUBMITTED', 'SUBMISSION_UNCONFIRMED'):
            raise ValueError('Submitted documents cannot be edited.')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 30000:
            raise ValueError('Enter a cover letter of at most 30,000 characters.')
        source = json.loads(run['snapshot'])
        edited = self.prepare(run['job_id'], source['profile'], request_key, True, source['optimization'],
                              letter_text_override=text.strip(), parent_id=identifier, profile_id=source.get('profile_id', 'default'))
        with self.store.connect() as db:
            db.execute("UPDATE application_runs SET state='SUPERSEDED',document_approved=0,form_approved=0 WHERE id=?", (identifier,))
        return edited

    def diff(self, identifier):
        source = self.snapshot(identifier)
        before, after = source['profile'], source['prepared_profile']
        return [{'section': key, 'before': before.get(key), 'after': after.get(key)}
                for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)]

    def validate_document_approval(self, identifier, document_hash):
        run = self.get(identifier)
        self.assert_current(run)
        if document_hash != run['document_hash'] or run['state'] not in ('DOCUMENT_REVIEW', 'READY_TO_FILL'):
            raise ValueError('This document approval is stale or unavailable.')

    def approve_documents(self, identifier, document_hash):
        self.validate_document_approval(identifier, document_hash)
        with self.store.connect() as db:
            db.execute("UPDATE application_runs SET state='READY_TO_FILL',document_approved=1,updated=? WHERE id=?", (now(), identifier))

    def record_form(self, identifier, form):
        run = self.get(identifier)
        self.assert_current(run)
        if not run['document_approved'] or run['state'] not in ('READY_TO_FILL', 'FILLING', 'FORM_REVIEW', 'NEEDS_USER', 'APPROVED'):
            raise ValueError('Review documents before filling a form.')
        if not isinstance(form, dict) or not isinstance(form.get('fields'), dict) or not isinstance(form.get('unanswered_required'), list):
            raise ValueError('Invalid filled-form report.')
        if len(json.dumps(form)) > 150000:
            raise ValueError('The application form is too large.')
        state = 'NEEDS_USER' if form['unanswered_required'] else 'FORM_REVIEW'
        with self.store.connect() as db:
            db.execute('UPDATE application_runs SET state=?,form=?,form_hash=?,form_approved=0,updated=? WHERE id=?',
                       (state, json.dumps(form, ensure_ascii=False), fingerprint(form), now(), identifier))

    def validate_form_approval(self, identifier, form_hash):
        run = self.get(identifier)
        self.assert_current(run)
        form = json.loads(run['form'])
        if run['state'] not in ('FORM_REVIEW', 'APPROVED') or not run['document_approved'] or form_hash != run['form_hash'] or form.get('unanswered_required'):
            raise ValueError('The form is incomplete or this approval is stale.')

    def approve_form(self, identifier, form_hash):
        self.validate_form_approval(identifier, form_hash)
        with self.store.connect() as db:
            db.execute("UPDATE application_runs SET form_approved=1,state='APPROVED',updated=? WHERE id=?", (now(), identifier))

    def record_submission(self, identifier, receipt):
        run = self.get(identifier)
        self.assert_current(run)
        confirmation = receipt.get('confirmation_text', '')
        url = receipt.get('confirmation_url', '')
        if (run['state'] not in ('APPROVED', 'SUBMITTING') or not run['document_approved'] or not run['form_approved'] or
                receipt.get('observed') is not True or not isinstance(confirmation, str) or not 5 <= len(confirmation) <= 10000 or
                urlsplit(url).hostname != urlsplit(self.store.get(run['job_id'])['url']).hostname):
            raise ValueError('A reviewed form and observed employer confirmation are required.')
        evidence = {**receipt, 'document_hash': run['document_hash'], 'form_hash': run['form_hash'], 'recorded_at': now()}
        with self.store.connect() as db:
            db.execute("UPDATE application_runs SET state='SUBMITTED',receipt=?,updated=? WHERE id=?", (json.dumps(evidence), now(), identifier))
        self.store.update(run['job_id'], 'SUBMITTED', note='Employer confirmation observed by application worker.', evidence=json.dumps(evidence))

    def _manual_state_available(self, run, db):
        if run['state'] not in ('DOCUMENT_REVIEW', 'READY_TO_FILL', 'FORM_REVIEW', 'APPROVED', 'NEEDS_USER', 'SUBMISSION_UNCONFIRMED'):
            return False
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='application_tasks'").fetchone() and db.execute(
                "SELECT 1 FROM application_tasks WHERE application_id=? AND state IN ('QUEUED','RUNNING')", (run['id'],)).fetchone():
            return False
        return not db.execute("SELECT 1 FROM application_runs WHERE job_id=? AND id<>? AND state IN ('SUBMITTING','SUBMITTED','SUBMISSION_UNCONFIRMED')",
                              (run['job_id'], run['id'])).fetchone()

    def manual_report_available(self, identifier):
        run = self.get(identifier)
        with self.store.connect() as db:
            if not self._manual_state_available(run, db):
                return False
        try:
            if run['state'] == 'SUBMISSION_UNCONFIRMED':
                self.verify_artifacts(run)
            else:
                self.assert_current(run)
        except (ValueError, FileNotFoundError):
            return False
        return True

    def record_manual_submission(self, identifier, note, attested, document_hash):
        """Record a user's historical report; this never creates an observed receipt."""
        if attested is not True:
            raise ValueError('Confirm that you checked the employer confirmation before recording your report.')
        if not isinstance(note, str) or not 5 <= len(note.strip()) <= 4000:
            raise ValueError('Enter a confirmation reference or evidence note between 5 and 4,000 characters.')
        note = note.strip()
        run = self.get(identifier)
        if document_hash != run['document_hash']:
            raise ValueError('This report belongs to a different document version. Reopen the application.')
        self.verify_artifacts(run)
        if run['state'] != 'SUBMISSION_UNCONFIRMED' and run['state'] != 'SUBMITTED':
            self.assert_current(run)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM application_runs WHERE id=?', (identifier,)).fetchone()
            prior = json.loads(current['manual_evidence'])
            if current['state'] == 'SUBMITTED':
                if prior.get('verification') == 'user-reported' and prior.get('note') == note:
                    return dict(current)
                raise ValueError('A completed submission record cannot be overwritten.')
            if current['state'] != run['state'] or not self._manual_state_available(current, db):
                raise ValueError('Wait for active worker tasks to finish. A submitted or active application cannot be reported again.')
            stamp = now()
            evidence = {'verification': 'user-reported', 'attested': True, 'note': note,
                        'reported_at': stamp, 'document_hash': run['document_hash'], 'profile_hash': run['profile_hash'],
                        'job_hash': run['job_hash'], 'form_hash': run['form_hash'], 'previous_state': run['state']}
            encoded = json.dumps(evidence, ensure_ascii=False)
            db.execute("UPDATE application_runs SET state='SUBMITTED',manual_evidence=?,error='',updated=? WHERE id=?", (encoded, stamp, identifier))
            status_note = 'User-reported submission; employer receipt was not independently verified.'
            # Resolving an earlier submission must not erase a later interview,
            # rejection or offer already recorded in the tracker.
            db.execute("UPDATE jobs SET status='SUBMITTED',note=?,evidence=?,updated=? WHERE id=? AND status NOT IN ('CONFIRMED','INTERVIEW','REJECTED','OFFER')",
                       (status_note, encoded, stamp, run['job_id']))
            db.execute("INSERT INTO events(job_id,created,status,note) VALUES(?,?,'SUBMITTED',?)", (run['job_id'], stamp, status_note))
        return self.get(identifier)

    def fail(self, identifier, error, uncertain=False):
        state = 'SUBMISSION_UNCONFIRMED' if uncertain else 'NEEDS_USER'
        with self.store.connect() as db:
            db.execute("UPDATE application_runs SET state=CASE WHEN state IN ('SUBMITTED','SUBMISSION_UNCONFIRMED') THEN state WHEN state='SUBMITTING' THEN 'SUBMISSION_UNCONFIRMED' ELSE ? END,error=?,updated=? WHERE id=?", (state, str(error)[:2000], now(), identifier))
