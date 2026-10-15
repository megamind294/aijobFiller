"""Authenticated job workflows backed by the existing Store."""
import re
import tempfile
from functools import wraps
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for

from .core import STATUSES, canonical_url, read_json, validate_profile
from .resume import prepare
from .service import ProcessLock
from .sources import read_jobs


def action_guard(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        try:
            with ProcessLock(Path(current_app.config['JOBPILOT_HOME']) / 'web-action.lock'):
                return view(*args, **kwargs)
        except (ValueError, OSError, RuntimeError, UnicodeError) as exc:
            message = str(exc)
            if 'already holds' in message:
                message = 'Another action is already running. Please wait and try again.'
            flash(message, 'error')
            return redirect(request.path.rsplit('/', 1)[0] if request.path.startswith('/jobs/') and
                            re.fullmatch(r'[a-f0-9]{16}', request.path.split('/')[2]) else
                            ('/sources' if request.path.startswith('/sources') else '/jobs'))
    return guarded


def validate_job(job):
    limits = {'url': 2048, 'title': 300, 'company': 300, 'location': 500, 'description': 50000, 'source': 100}
    if not isinstance(job, dict):
        raise ValueError('Each job must be an object.')
    clean = {}
    for name, limit in limits.items():
        value = job.get(name, '')
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f'Invalid or too-long {name} field.')
        clean[name] = value.strip()
    if not all(clean[k] for k in ('url', 'title', 'company')):
        raise ValueError('URL, title, and company are required.')
    clean['url'] = canonical_url(clean['url'])
    return clean


def register_jobs(app):
    store = app.extensions['jobpilot_store']
    home = Path(app.config['JOBPILOT_HOME'])

    def get_job(key):
        if not re.fullmatch(r'[a-f0-9]{16}', key):
            abort(404)
        try:
            return store.get(key)
        except ValueError:
            abort(404)

    @app.get('/jobs')
    def jobs():
        query = request.args.get('q', '').strip().casefold()
        status = request.args.get('status', '')
        rows = [j for j in store.rows() if
                (not status or j['status'] == status) and
                (not query or query in ' '.join((j['title'], j['company'], j['location'])).casefold())]
        return render_template('jobs.html', jobs=rows, query=request.args.get('q', ''),
                               selected_status=status, statuses=sorted(STATUSES))

    @app.post('/jobs/add')
    @action_guard
    def add_job():
        key = store.add(validate_job(request.form))
        flash('Opportunity added to your queue.', 'success')
        return redirect(url_for('job_detail', key=key))

    @app.post('/jobs/import')
    @action_guard
    def import_jobs():
        upload = request.files.get('file')
        if not upload or not upload.filename:
            raise ValueError('Choose a JSON or CSV file to import.')
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ('.json', '.csv'):
            raise ValueError('Choose a .json or .csv file.')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ('import' + suffix)
            upload.save(path)
            imported = read_jobs(path)
            if len(imported) > 1000:
                raise ValueError('Import at most 1,000 opportunities at a time.')
            validated = [validate_job(job) for job in imported]
            for job in validated:
                store.add(job)
        flash(f'Imported {len(validated)} opportunities. Existing progress was preserved.', 'success')
        return redirect(url_for('jobs'))

    @app.get('/jobs/<key>')
    def job_detail(key):
        job = get_job(key)
        with store.connect() as db:
            events = [dict(row) for row in db.execute(
                'SELECT created,status,note FROM events WHERE job_id=? ORDER BY rowid DESC', (key,))]
        return render_template('job.html', job=job, events=events, statuses=sorted(STATUSES))

    @app.post('/jobs/<key>/prepare')
    @action_guard
    def prepare_job(key):
        get_job(key)
        try:
            profile = validate_profile(read_json(home / 'profile.json'))
        except FileNotFoundError:
            raise ValueError('Set up and verify your profile before preparing a resume.') from None
        prepare(store, key, profile, use_ai=False)
        flash('Your resume is ready. Review the PDF before applying.', 'success')
        return redirect(url_for('job_detail', key=key))

    @app.post('/jobs/<key>/status')
    @action_guard
    def update_status(key):
        get_job(key)
        status = request.form.get('status', '')
        note = request.form.get('note', '').strip()
        if status not in STATUSES or not note or len(note) > 2000:
            raise ValueError('Choose a valid status and add a note of at most 2,000 characters.')
        if request.form.get('confirmed') != 'yes':
            raise ValueError('Confirm that you checked this status before recording it.')
        store.update(key, status, note='User report: ' + note)
        flash('Application status recorded.', 'success')
        return redirect(url_for('job_detail', key=key))

    @app.get('/jobs/<key>/resume')
    def download_resume(key):
        job = get_job(key)
        if not job['resume']:
            abort(404)
        path = Path(job['resume']).resolve()
        expected = (home / 'resumes' / key / 'resume.pdf').resolve()
        if path != expected or not path.is_relative_to(home.resolve()) or not path.is_file():
            abort(404)
        return send_file(path, mimetype='application/pdf', as_attachment=True,
                         download_name=f'JobPilot-resume-{key}.pdf')

    @app.get('/export')
    @action_guard
    def export():
        return send_file(store.export(), as_attachment=True, download_name='JobPilot-applications.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
