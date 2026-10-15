"""Review exact immutable packages; browser work runs outside HTTP requests."""
import json
import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from .applications import Applications
from .billing import CreditLedger
from .core import read_json
from .worker import TaskQueue
from .workspace import current_home, current_store


def billing_context():
    ledger = current_app.extensions.get('jobpilot_credit_ledger') or CreditLedger(Path(current_app.config['JOBPILOT_HOME']))
    account = getattr(g, 'jobpilot_account', None)
    if not account:
        abort(401)
    return ledger, account['id'], account.get('role') == 'owner'


def application_action(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except (ValueError, FileNotFoundError, TypeError) as exc:
            return render_template('application_error.html', message=str(exc)), 400
    return guarded


def approve_and_queue(identifier, approval_hash, action):
    """The single request-scoped approval, budget and queue path for web/API/MCP."""
    service = Applications(current_store())
    if action == 'documents':
        service.approve_documents(identifier, approval_hash)
        worker_action = 'fill'
    elif action == 'form':
        service.approve_form(identifier, approval_hash)
        worker_action = 'submit'
    else:
        raise ValueError('Unknown application approval.')
    from .automation import Automation
    ledger, account_id, owner = billing_context()
    limits = Automation(current_store(), ledger, account_id, owner=owner).settings()
    day = datetime.now(timezone.utc).astimezone(ZoneInfo(limits['timezone'])).date().isoformat()
    ledger.reserve(account_id, 'application:' + identifier, day, limits['daily_cap'], owner=owner)
    TaskQueue(current_store()).enqueue(identifier, worker_action)
    return service.get(identifier)


def register_applications(app):
    def can_restart(service, run):
        tasks = TaskQueue(service.store).rows()
        tasks = [task for task in tasks if task['application_id'] == run['id']]
        return (run['state'] == 'NEEDS_USER' and bool(tasks) and
                all(not task['attempted'] and task['state'] in ('PAUSED', 'DONE') for task in tasks))

    def package(identifier):
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            abort(404)
        service = Applications(current_store())
        try:
            return service, service.get(identifier)
        except ValueError:
            abort(404)

    @app.get('/applications')
    @app.get('/dashboard/applications')
    def applications_index():
        service = Applications(current_store())
        rows = service.rows()
        return render_template('applications.html', applications=rows)

    @app.post('/applications/prepare/<job_id>')
    @application_action
    def application_prepare(job_id):
        service = Applications(current_store())
        run = service.prepare(job_id, read_json(current_home() / 'profile.json'), request.form.get('request_key', secrets.token_hex(16)),
                              request.form.get('cover_letter', 'yes') == 'yes', request.form.get('optimization', 'honest'))
        return redirect(url_for('application_review', identifier=run['id']))

    @app.get('/applications/<identifier>')
    @app.get('/dashboard/applications/<identifier>')
    def application_review(identifier):
        service, run = package(identifier)
        stale = ''
        try:
            service.assert_current(run)
        except (ValueError, FileNotFoundError) as exc:
            stale = str(exc)
        return render_template('application_review.html', run=run, source=service.snapshot(identifier),
                               diff=service.diff(identifier), form=json.loads(run['form']), receipt=json.loads(run['receipt']),
                               stale=stale, request_key=secrets.token_hex(16), job=current_store().get(run['job_id']),
                               can_restart=can_restart(service, run))

    @app.post('/applications/<identifier>/restart-review')
    @application_action
    def application_restart_review(identifier):
        service, run = package(identifier)
        if not can_restart(service, run):
            raise ValueError('Only a paused application with no submission attempt can start a new review.')
        source = service.snapshot(identifier)
        fresh = service.prepare(run['job_id'], read_json(current_home() / 'profile.json'),
                                request.form.get('request_key', ''), bool(run['letter_path']),
                                source['optimization'], parent_id=identifier)
        return redirect(url_for('application_review', identifier=fresh['id']))

    @app.get('/applications/<identifier>/artifacts/<kind>')
    @application_action
    def application_artifact(identifier, kind):
        service, run = package(identifier)
        service.verify_artifacts(run)
        path = service.artifact(identifier, kind)
        return send_file(path, as_attachment=True, download_name=path.name,
                         mimetype='application/json' if kind == 'source' else 'application/pdf')

    @app.post('/applications/<identifier>/documents/approve')
    @application_action
    def application_approve_documents(identifier):
        service, run = package(identifier)
        approve_and_queue(identifier, request.form.get('document_hash', ''), 'documents')
        flash('Documents approved. The worker will prepare the supported form for your separate review.', 'success')
        return redirect(url_for('application_review', identifier=identifier))

    @app.post('/applications/<identifier>/letter')
    @application_action
    def application_edit_letter(identifier):
        service, run = package(identifier)
        edited = service.edit_letter(identifier, request.form.get('cover_letter', ''), request.form.get('request_key', ''))
        ledger, account_id, owner = billing_context()
        try:
            ledger.settle(account_id, 'application:' + identifier, False)
        except ValueError:
            pass
        return redirect(url_for('application_review', identifier=edited['id']))

    @app.post('/applications/<identifier>/submit')
    @application_action
    def application_submit(identifier):
        service, run = package(identifier)
        approve_and_queue(identifier, request.form.get('form_hash', ''), 'form')
        flash('Submission approved for this exact form and document version. Success requires an employer receipt.', 'success')
        return redirect(url_for('application_review', identifier=identifier))
