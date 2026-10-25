"""Durable leased application tasks. Submission attempts are never retried."""
import json
import secrets
import time
from urllib.parse import urlsplit

from .applications import Applications, fingerprint
from .browser import adapter_scope, captcha_required, confirmed, fill_adapter, fill_common, FIELD_INFO
from .core import now, origin


class TaskQueue:
    def __init__(self, store):
        self.store = store
        Applications(store)
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS application_tasks (
                id TEXT PRIMARY KEY, application_id TEXT NOT NULL, action TEXT NOT NULL,
                state TEXT NOT NULL, owner TEXT DEFAULT '', lease REAL DEFAULT 0,
                attempted INTEGER DEFAULT 0, created TEXT, updated TEXT,
                UNIQUE(application_id,action))''')

    def enqueue(self, application_id, action):
        if action not in ('fill', 'submit'):
            raise ValueError('Unknown worker action.')
        identifier = secrets.token_hex(16)
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO application_tasks(id,application_id,action,state,created,updated) VALUES(?,?,?,?,?,?)',
                       (identifier, application_id, action, 'QUEUED', now(), now()))
            return db.execute('SELECT id FROM application_tasks WHERE application_id=? AND action=?', (application_id, action)).fetchone()[0]

    def rows(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM application_tasks ORDER BY created DESC')]

    def claim(self, owner, stamp=None, lease_seconds=120):
        stamp = time.time() if stamp is None else stamp
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            expired = db.execute("SELECT application_id FROM application_tasks WHERE state='RUNNING' AND lease<=? AND attempted=1", (stamp,)).fetchall()
            db.execute("UPDATE application_tasks SET state='UNCERTAIN',updated=? WHERE state='RUNNING' AND lease<=? AND attempted=1", (now(), stamp))
            for row in expired:
                db.execute("UPDATE application_runs SET state='SUBMISSION_UNCONFIRMED',error='Worker lease expired after submission began; verify employer portal.',updated=? WHERE id=? AND state='SUBMITTING'", (now(), row[0]))
            row = db.execute("SELECT * FROM application_tasks WHERE state='QUEUED' OR (state='RUNNING' AND lease<=? AND attempted=0) ORDER BY created,id LIMIT 1", (stamp,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE application_tasks SET state='RUNNING',owner=?,lease=?,updated=? WHERE id=?", (owner, stamp + lease_seconds, now(), row['id']))
            result = dict(row)
            result.update(owner=owner, lease=stamp + lease_seconds, state='RUNNING')
            return result

    def mark_attempt(self, identifier, owner):
        with self.store.connect() as db:
            count = db.execute("UPDATE application_tasks SET attempted=1,updated=? WHERE id=? AND owner=? AND state='RUNNING' AND attempted=0", (now(), identifier, owner)).rowcount
            if not count:
                raise ValueError('Worker task lease is no longer owned or submission already attempted.')

    def complete(self, identifier, owner, state='DONE'):
        if state not in ('DONE', 'PAUSED', 'UNCERTAIN'):
            raise ValueError('Invalid task result.')
        with self.store.connect() as db:
            if not db.execute("UPDATE application_tasks SET state=?,updated=? WHERE id=? AND owner=? AND state='RUNNING'", (state, now(), identifier, owner)).rowcount:
                raise ValueError('Worker task lease is no longer owned.')


def observed_form(page, document_hash):
    fields, missing = {}, []
    for index in range(page.locator('input,textarea,select').count()):
        field = page.locator('input,textarea,select').nth(index)
        meta = field.evaluate(FIELD_INFO)
        if not field.is_enabled() or meta['type'] in ('hidden', 'submit', 'button', 'reset'):
            continue
        if not field.is_visible() and meta['type'] != 'file':
            continue
        name = meta['name'] or meta['id'] or meta['label'] or str(index)
        if meta['type'] == 'file':
            value = field.evaluate('(el)=>Array.from(el.files).map(f=>f.name).join(",")')
        elif meta['type'] in ('checkbox', 'radio'):
            value = field.is_checked()
        else:
            value = field.input_value()
        fields[name] = {'label': meta['label'], 'value': value, 'required': meta['required'], 'type': meta['type']}
        if meta['required'] and not field.evaluate('(el)=>el.checkValidity()'):
            missing.append(meta['label'] or name)
    return {'fields': fields, 'unanswered_required': missing, 'document_hash': document_hash}


class ApplicationWorker:
    def __init__(self, store, adapters=None, *, browser_executable=None, ledger=None, account_id=None, automation=None, lock_root=None):
        self.store, self.apps, self.queue = store, Applications(store), TaskQueue(store)
        self.adapters = adapters or {}
        self.browser_executable = browser_executable
        self.owner = secrets.token_hex(16)
        self.ledger, self.account_id = ledger, account_id
        self.automation = automation
        self.lock_root = lock_root or store.home

    def run_once(self):
        from pathlib import Path
        from .service import ProcessLock
        if self.ledger and self.account_id != 'owner' and not self.ledger.has_workspace_access(self.account_id):
            return False
        try:
            with ProcessLock(Path(self.lock_root) / 'heavy-work.lock'):
                return self._run_once()
        except RuntimeError as exc:
            if 'already holds' in str(exc):
                return False
            raise

    def _run_once(self):
        task = self.queue.claim(self.owner)
        if not task:
            return False
        identifier = task['application_id']
        attempted = False
        try:
            run = self.apps.get(identifier)
            if self.ledger and self.account_id != 'owner':
                self.ledger.assert_paid_reservation(self.account_id, 'application:' + identifier)
            self.apps.assert_current(run)
            if run['state'] in ('SUBMITTED', 'SUBMISSION_UNCONFIRMED', 'SUBMITTING'):
                raise ValueError('Application already submitted or uncertain; automatic retry is blocked.')
            url = self.store.get(run['job_id'])['url']
            if urlsplit(url).hostname not in ('localhost', '127.0.0.1', '::1') and not (self.ledger and self.account_id):
                raise ValueError('External application workers require account credit accounting.')
            adapter = self.adapters.get(origin(url))
            if not adapter or adapter.get('origin') != origin(url):
                raise ValueError('Unsupported employer form. Open the employer page and continue under your supervision.')
            if not run['document_approved']:
                raise ValueError('Review exact documents before form filling.')
            if task['action'] == 'submit' and (run['state'] != 'APPROVED' or not run['form_approved']):
                raise ValueError('Explicit form approval or activated automatic lane approval is required.')
            with self.store.connect() as db:
                automatic = False
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='automation_applications'").fetchone():
                    columns = {row['name'] for row in db.execute('PRAGMA table_info(automation_applications)')}
                    lane_run = db.execute('SELECT * FROM automation_applications WHERE application_id=?', (identifier,)).fetchone()
                    # Creation mode is immutable; changing a lane to review-first
                    # must not bless an old automatically approved submission.
                    automatic = bool(lane_run and ('submission_mode' not in columns or lane_run['submission_mode'] != 'review-first'))
            if automatic and task['action'] == 'submit' and (not self.automation or not self.automation.authorized_automatic(identifier)):
                raise ValueError('Automatic lane authorization was revoked. Review this application manually.')
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                options = {'headless': True}
                if self.browser_executable:
                    options['executable_path'] = self.browser_executable
                browser = playwright.chromium.launch(**options)
                try:
                    context = browser.new_context(accept_downloads=False)
                    # Block navigation/subresources outside the configured origin. No credential reuse.
                    def permitted(route):
                        if origin(route.request.url) == adapter['origin']:
                            route.continue_()
                        else:
                            route.abort()
                    context.route('**/*', permitted)
                    page = context.new_page()
                    page.goto(url, wait_until='domcontentloaded', timeout=30000)
                    adapter_scope(page, adapter)
                    if captcha_required(page) or page.locator('input[type=password]').count():
                        raise ValueError('CAPTCHA or login detected. Continue in a supervised browser.')
                    if page.locator('iframe').count() or adapter.get('frame_url_contains'):
                        raise ValueError('Embedded application forms require supervised review.')
                    source = self.apps.snapshot(identifier)
                    fill_common(page, source['profile'], self.apps.artifact(identifier, 'resume'))
                    fill_adapter(page, {**adapter, 'fields': [m for m in adapter.get('fields', []) if m.get('kind') != 'cover_letter']}, source['profile'], self.apps.artifact(identifier, 'resume'))
                    for mapping in adapter.get('fields', []):
                        if mapping.get('kind') == 'cover_letter':
                            page.locator(mapping['selector']).set_input_files(str(self.apps.artifact(identifier, 'letter')))
                    form = observed_form(page, run['document_hash'])
                    if task['action'] == 'fill':
                        self.apps.record_form(identifier, form)
                        if automatic and not form['unanswered_required'] and self.automation and self.automation.authorized_automatic(identifier):
                            current = self.apps.get(identifier)
                            self.apps.approve_form(identifier, current['form_hash'])
                            self.queue.enqueue(identifier, 'submit')
                    else:
                        if form['unanswered_required'] or fingerprint(form) != run['form_hash']:
                            self.apps.record_form(identifier, form)
                            raise ValueError('The live form changed or needs required answers. Review it again.')
                        self.apps.assert_current(self.apps.get(identifier))
                        if confirmed(page, adapter):
                            raise ValueError('Receipt already visible; verify the portal without a second submission.')
                        target = page.locator(adapter.get('submit_selector', ''))
                        if not adapter.get('success_selector') or not adapter.get('success_text') or target.count() != 1 or not target.is_enabled():
                            raise ValueError('Adapter needs an unambiguous submit control and receipt.')
                        with self.store.connect() as db:
                            db.execute('BEGIN IMMEDIATE')
                            blocked = db.execute("SELECT id FROM application_runs WHERE job_id=? AND id<>? AND state IN ('SUBMITTING','SUBMITTED','SUBMISSION_UNCONFIRMED')", (run['job_id'], identifier)).fetchone()
                            if blocked:
                                raise ValueError('Another application for this job is already submitted or uncertain.')
                            if not db.execute("UPDATE application_runs SET state='SUBMITTING',updated=? WHERE id=? AND state='APPROVED'", (now(), identifier)).rowcount:
                                raise ValueError('Application approval is no longer available.')
                        self.queue.mark_attempt(task['id'], self.owner)
                        attempted = True
                        target.click(timeout=15000)
                        page.locator(adapter['success_selector']).wait_for(state='visible', timeout=15000)
                        if not confirmed(page, adapter):
                            raise ValueError('Employer receipt was not confirmed.')
                        self.apps.record_submission(identifier, {'observed': True, 'confirmation_url': page.url,
                            'confirmation_text': page.locator(adapter['success_selector']).inner_text(), 'adapter_origin': adapter['origin']})
                        if self.ledger and self.account_id:
                            self.ledger.settle(self.account_id, 'application:' + identifier, True)
                finally:
                    browser.close()
            self.queue.complete(task['id'], self.owner)
        except Exception as exc:
            # Once an attempt begins, uncertainty is durable and no automatic retry is possible.
            try:
                current = self.apps.get(identifier)
            except ValueError:
                self.queue.complete(task['id'], self.owner, 'PAUSED')
                return True
            uncertain = attempted or current['state'] in ('SUBMITTING', 'SUBMISSION_UNCONFIRMED')
            self.apps.fail(identifier, str(exc), uncertain=uncertain)
            self.queue.complete(task['id'], self.owner, 'UNCERTAIN' if uncertain else 'PAUSED')
            if self.ledger and self.account_id and not uncertain and current['state'] != 'SUBMITTED':
                try:
                    self.ledger.settle(self.account_id, 'application:' + identifier, False)
                except ValueError:
                    pass
        return True


def run_workspaces(root, *, account_id=None, max_tasks=1, run_lanes=False, config=None):
    """One bounded pass over existing server account workspaces."""
    import re
    from pathlib import Path
    from .accounts import Accounts
    from .automation import Automation
    from .billing import CreditLedger
    from .core import Store
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = config or {}
    adapters = config.get('adapters', {})
    if not isinstance(adapters, dict):
        raise ValueError('adapters must be a map keyed by exact origin')
    accounts = Accounts(root)
    if account_id is not None:
        if account_id != 'owner' and (not re.fullmatch('[a-f0-9]{32}', account_id) or not accounts.get(account_id)):
            raise ValueError('Unknown account')
        identifiers = [account_id]
    else:
        identifiers = ['owner']
        directory = root / 'accounts'
        if directory.is_dir() and not directory.is_symlink():
            identifiers.extend(item.name for item in directory.iterdir()
                               if item.is_dir() and not item.is_symlink() and re.fullmatch('[a-f0-9]{32}', item.name) and accounts.get(item.name))
    ledger = CreditLedger(root)
    processed = 0
    for identifier in identifiers:
        owner = identifier == 'owner'
        home = root if owner else root / 'accounts' / identifier
        if any(part.is_symlink() for part in (home, *home.parents)):
            continue
        store = Store(home)
        # Receipt persistence and billing use separate databases. A crash between them
        # must settle the existing reservation without revisiting the employer form.
        for recorded in Applications(store).rows():
            if recorded['state'] == 'SUBMITTED':
                try:
                    receipt = json.loads(recorded['receipt'])
                    if receipt.get('observed') is True:
                        ledger.settle(identifier, 'application:' + recorded['id'], True)
                    elif json.loads(recorded['manual_evidence']).get('verification') == 'user-reported':
                        ledger.settle(identifier, 'application:' + recorded['id'], False)
                except (ValueError, TypeError):
                    pass  # Legacy packages can predate credit reservations.
        if not owner and not ledger.has_workspace_access(identifier):
            continue
        subscription = ledger.subscription(identifier)
        automation = Automation(store, ledger, identifier, owner=owner,
                                entitled=subscription.get('tier') == 'power' and subscription.get('status') in ('active', 'trialing'), adapters=adapters)
        if run_lanes and any(lane['active'] for lane in automation.lanes()):
            try:
                automation.run()
            except (ValueError, FileNotFoundError):
                pass  # Invalid profile/expired entitlement leaves the lane paused; queued tasks still get checked.
        worker = ApplicationWorker(store, adapters, ledger=ledger, account_id=identifier, automation=automation,
                                   browser_executable=config.get('browser_executable'), lock_root=root)
        for _ in range(max_tasks):
            if not worker.run_once():
                break
            processed += 1
    return processed


def main(argv=None):
    """Operator-only command. Default is a single bounded on-demand pass."""
    import argparse
    import os
    from pathlib import Path
    from .application_config import load_worker_config
    parser = argparse.ArgumentParser(description='Consume queued applications; no lane activates automatically.')
    parser.add_argument('--root', '--home', dest='root', default=os.environ.get('JOBPILOT_ROOT'), help='Private JobPilot storage root')
    parser.add_argument('--account', help='Optional existing account ID; omit to process existing workspaces')
    parser.add_argument('--max-tasks', type=int, default=1)
    parser.add_argument('--run-lanes', action='store_true', help='Evaluate explicitly activated lanes before each queue pass')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='One bounded pass (default)')
    mode.add_argument('--watch', action='store_true', help='Consume queued work every five seconds until stopped')
    args = parser.parse_args(argv)
    if not args.root or not 1 <= args.max_tasks <= 100:
        parser.error('Supply --home/--root and --max-tasks between 1 and 100')
    root = Path(args.root).resolve()
    config = load_worker_config(root)
    try:
        while True:
            run_workspaces(root, account_id=args.account, max_tasks=args.max_tasks, run_lanes=args.run_lanes, config=config)
            if not args.watch:
                return 0
            time.sleep(5)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
