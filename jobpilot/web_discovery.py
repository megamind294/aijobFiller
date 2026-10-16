"""Authenticated discovery routes using the caller's account-scoped workspace."""
import re
import time
from functools import wraps
from flask import abort, flash, redirect, render_template, request, url_for
from .core import read_json, write_json
from .discovery_catalog import (catalog_rows, explain_match, filter_jobs, ingest_jobs,
                                import_posting, normalize_sources, set_preference)
from .sources import DiscoveryBudget, discover
from .workspace import current_home, current_store
from .service import ProcessLock

# Public boards give a new workspace a usable starting point without API keys.
STARTER_SOURCES = [{'type':'ashby', 'board':board, 'company':company, 'region':'global'}
                   for board, company in [('ashby','Ashby'), ('supabase','Supabase'),
                       ('n8n','n8n'), ('replit','Replit'), ('posthog','PostHog'), ('lovable','Lovable')]]


def register_discovery(app):
    def profile():
        try:
            data = read_json(current_home() / 'profile.json')
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError): return {}

    def registry():
        try:
            return normalize_sources(read_json(current_home() / 'sources.json').get('sources', []))
        except FileNotFoundError: return []

    def guarded(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            try:
                with ProcessLock(current_home() / 'web-action.lock'):
                    return view(*args, **kwargs)
            except (ValueError, OSError, RuntimeError, TypeError) as exc:
                flash(str(exc), 'error')
                return redirect(url_for('browse'))
        return wrapped

    def find_job(key):
        if not re.fullmatch(r'[a-f0-9]{16}', key): abort(404)
        row = next((row for row in catalog_rows(current_store()) if row['id'] == key), None)
        if row is None: abort(404)
        return row

    @app.get('/browse')
    def browse():
        try:
            companies = registry()
        except (ValueError, OSError, AttributeError) as exc:
            companies = []
            flash('Source registry needs correction: ' + str(exc), 'error')
        try:
            jobs = filter_jobs(catalog_rows(current_store()), request.args)
        except ValueError as exc:
            jobs = []
            flash(str(exc), 'error')
        facts = profile()
        for job in jobs: job['match'] = explain_match(job, facts)
        try:
            refreshed = read_json(current_home() / 'discovery-refresh.json').get('display_time', '')
        except (OSError, ValueError, AttributeError):
            refreshed = ''
        return render_template('browse.html', jobs=jobs, registry=companies, filters=request.args,
                               source_count=len(companies or STARTER_SOURCES), refreshed=refreshed)

    @app.post('/browse/search')
    @guarded
    def browse_search():
        from datetime import datetime, timezone
        query = request.form.get('q', '').strip()[:300]
        companies = registry() or STARTER_SOURCES
        try:
            cache = read_json(current_home() / 'discovery-refresh.json')
        except (OSError, ValueError):
            cache = {}
        if (cache.get('sources') != companies or
                time.time() - float(cache.get('timestamp', 0)) >= 300):
            jobs, warnings = discover({'sources':companies, 'title_keywords':[], 'location_keywords':[]},
                                      budget=DiscoveryBudget(), safe_transport=True)
            if jobs:
                keys = ingest_jobs(current_store(), jobs)
                write_json(current_home() / 'discovery-refresh.json', {
                    'timestamp':time.time(), 'sources':companies,
                    'display_time':datetime.now(timezone.utc).strftime('%d %b, %H:%M UTC')})
                flash(f'Updated {len(keys)} live postings from {len(companies)} company boards.', 'success')
            elif warnings:
                flash('Live job sources could not be refreshed. Your saved jobs are still available. Please try again.', 'error')
            else:
                flash('These company boards currently have no open postings.', 'info')
            for warning in warnings[:6]:
                flash('Source issue: ' + warning, 'error')
        return redirect(url_for('browse', q=query))

    @app.post('/browse/discover')
    @guarded
    def browse_discover():
        companies = registry()
        if request.form.get('operation') == 'remove':
            index = int(request.form.get('index', '-1'))
            if not 0 <= index < len(companies): raise ValueError('Unknown company board.')
            companies.pop(index)
        elif request.form.get('board', '').strip():
            companies = normalize_sources(companies + [{'type':request.form.get('source_type',''),
                'board':request.form['board'], 'company':request.form.get('company',''), 'region':request.form.get('region','global')}])
        try:
            config = read_json(current_home() / 'sources.json')
        except FileNotFoundError:
            config = {}
        config['sources'] = companies
        write_json(current_home() / 'sources.json', config)
        if request.form.get('operation') == 'remove':
            flash('Company board removed. Existing jobs were retained.', 'success')
            return redirect(url_for('browse'))
        if not companies: raise ValueError('Add a public company board to discover jobs.')
        jobs, warnings = discover({'sources':companies, 'title_keywords':[], 'location_keywords':[]},
                                   budget=DiscoveryBudget(), safe_transport=True)
        keys = ingest_jobs(current_store(), jobs)
        flash(f'Refreshed {len(keys)} opportunities. Existing application progress was preserved.', 'success')
        for warning in warnings[:25]: flash(warning, 'error')
        return redirect(url_for('browse'))

    @app.post('/browse/import-url')
    @guarded
    def browse_import_url():
        if request.form.get('mode') == 'manual':
            job = {name:request.form.get(name,'') for name in ('url','title','company','location','description')}
            job['source'] = 'manual'
        else:
            job = import_posting(request.form.get('url',''))
        key = ingest_jobs(current_store(), [job])[0]
        flash('Opportunity imported. Review its source and match explanation.', 'success')
        return redirect(url_for('browse_detail', key=key))

    @app.get('/browse/<key>')
    def browse_detail(key):
        job = find_job(key)
        return render_template('browse_detail_fragment.html' if request.args.get('fragment') == '1' else 'browse_detail.html',
                               job=job, match=explain_match(job, profile()))

    @app.post('/browse/<key>/action')
    @guarded
    def browse_action(key):
        find_job(key)
        set_preference(current_store(), key, request.form.get('action',''))
        flash('Opportunity preference updated.', 'success')
        return redirect(url_for('browse'))
