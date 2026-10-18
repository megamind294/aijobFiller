"""Profile configuration and explicitly requested public-feed discovery."""
import json
import re
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request

from .core import read_json, validate_profile, write_json
from .sources import DiscoveryBudget, discover
from .web_jobs import action_guard, validate_job
from .resume_uploads import load_resume


def validate_profile_shape(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get('personal', {}), dict):
        raise ValueError('Profile and personal details must be objects.')
    for key, value in profile.get('personal', {}).items():
        if not isinstance(value, str):
            raise ValueError(f'Personal field {key} must be text.')
    if not isinstance(profile.get('summary', ''), str):
        raise ValueError('Summary must be text.')
    for section in ('skills', 'languages', 'summary_variants'):
        items = profile.get(section, [])
        if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
            raise ValueError(f'{section} must be an array of text values.')
    for section in ('experience', 'projects', 'education'):
        items = profile.get(section, [])
        if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
            raise ValueError(f'{section} must be an array of objects.')
        for item in items:
            for key, value in item.items():
                if key == 'bullets':
                    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                        raise ValueError(f'{section} bullets must be an array of text values.')
                elif not isinstance(value, str):
                    raise ValueError(f'{section} {key} must be text.')
    for section in ('answers', 'custom'):
        value = profile.get(section, {})
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
            raise ValueError(f'{section} must be an object with text values.')


def validate_sources(config):
    if not isinstance(config, dict):
        raise ValueError('Source configuration must be an object.')
    sources = config.get('sources', [])
    if not isinstance(sources, list) or len(sources) > 3:
        raise ValueError('Configure up to three company boards per discovery request.')
    for source in sources:
        if (not isinstance(source, dict) or source.get('type') not in ('greenhouse', 'lever') or
                not isinstance(source.get('board'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', source['board']) or
                source.get('region', 'global') not in ('global', 'eu')):
            raise ValueError('Use Greenhouse or Lever sources with a valid company board token and global/eu region.')
    for key in ('title_keywords', 'location_keywords'):
        values = config.get(key, [])
        if not isinstance(values, list) or len(values) > 50 or any(not isinstance(v, str) or len(v) > 100 for v in values):
            raise ValueError(f'{key} must contain at most 50 short text values.')
    return config


def register_settings(app):
    from werkzeug.local import LocalProxy
    from .workspace import current_home
    home = LocalProxy(current_home)
    sources_path = LocalProxy(lambda: current_home() / 'sources.json')

    @app.route('/profile', methods=['GET', 'POST'])
    def profile():
        from .workspace import current_home
        from .profile_forms import parse_profile_form, profile_errors, PERSONAL, SECTIONS, CUSTOM, DOCUMENT_SECTIONS, DOCUMENT_HEADER_FIELDS, document_preferences
        from .profile_vault import ProfileVault
        scoped_home = current_home()
        scoped_path = scoped_home / 'profile.json'
        vault = ProfileVault(scoped_home)
        vault.ensure_legacy()
        key = request.form.get('profile_id') or request.args.get('profile_id')
        try:
            selected = vault.get(key) if key else vault.default()
        except ValueError:
            from flask import abort
            abort(404)
        candidate = selected['profile'] if selected else (read_json(scoped_path) if scoped_path.exists() else {'personal':{}, 'skills':[], 'facts_verified':False})
        errors = {}
        if request.method == 'POST':
            try:
                candidate = parse_profile_form(request.form, candidate)
                validate_profile_shape(candidate)
                errors = profile_errors(candidate)
                if errors: raise ValueError('Check the highlighted fields. Your edits are preserved below.')
                if candidate['facts_verified']: validate_profile(candidate)
                from .service import ProcessLock
                with ProcessLock(scoped_home / 'web-action.lock'):
                    selected = vault.save(candidate, selected['id'] if selected else None,
                                          expected_version=request.form.get('profile_version') or None)
                flash('Profile saved. ' + ('Your verified facts are ready for resumes.' if candidate['facts_verified'] else 'Draft saved; verify your facts before preparing resumes.'), 'success')
                if request.form.get('save_action') == 'preview':
                    return redirect(f"/profiles/{selected['id']}/versions/{selected['version']}/preview")
                return redirect('/profile?profile_id=' + selected['id'])
            except (ValueError, TypeError, RuntimeError) as exc:
                flash('Another action is already running. Try again shortly.' if isinstance(exc, RuntimeError) else str(exc), 'error')
                if 'profile_json' in request.form:
                    candidate = selected['profile'] if selected else {'personal':{},'skills':[],'facts_verified':False}
                else:
                    recovery = {k:v for k,v in request.form.items() if not k.endswith('_json') and not k.startswith('preference_') and not k.startswith('answers_')}
                    try: candidate = parse_profile_form(recovery, candidate)
                    except ValueError: pass
                if not isinstance(candidate,dict): candidate = {'personal':{}, 'skills':[], 'facts_verified':False}
        answer_rows = list(candidate.get('answers',{}).items()) or [('', '')]
        if request.method == 'POST' and 'profile_json' not in request.form:
            indices = sorted({int(m.group(1)) for field in request.form if (m:=re.fullmatch(r'answers_(\d+)_question',field))})
            if indices: answer_rows = [(request.form.get(f'answers_{i}_question',''),request.form.get(f'answers_{i}_answer','')) for i in indices]
        return render_template('profile.html', profile=candidate, selected=selected, profiles=vault.list(), errors=errors, answer_rows=answer_rows,
                               personal_fields=PERSONAL, repeat_sections=SECTIONS, custom_fields=CUSTOM,
                               preferences=document_preferences(candidate.get('document_preferences')),
                               document_sections=DOCUMENT_SECTIONS, document_header_fields=DOCUMENT_HEADER_FIELDS,
                               submitted=request.form if request.method == 'POST' else None,
                               uploaded_resume=load_resume(scoped_home))

    @app.route('/sources', methods=['GET', 'POST'])
    def sources():
        config = read_json(sources_path) if sources_path.exists() else {
            'sources': [], 'title_keywords': ['react', 'frontend', 'front-end'],
            'location_keywords': ['poland', 'krak', 'warsaw', 'warszawa']}
        raw = json.dumps(config, indent=2, ensure_ascii=False)
        if request.method == 'POST':
            raw = request.form.get('sources_json', '')
            try:
                from .service import ProcessLock
                validated = validate_sources(json.loads(raw))
                with ProcessLock(home / 'web-action.lock'):
                    write_json(sources_path, validated)
                flash('Company sources saved.', 'success')
                return redirect('/sources')
            except (ValueError, TypeError, RuntimeError) as exc:
                flash('Another action is already running. Try again shortly.' if isinstance(exc, RuntimeError) else str(exc), 'error')
        return render_template('sources.html', sources_json=raw, source_count=len(config.get('sources', [])))

    @app.post('/sources/discover')
    @action_guard
    def discover_jobs():
        if not sources_path.exists():
            raise ValueError('Add company boards and save your sources first.')
        config = validate_sources(read_json(sources_path))
        if not config.get('sources'):
            raise ValueError('Add a company board before running discovery.')
        found, errors = discover(config, budget=DiscoveryBudget())
        from .workspace import current_store
        store = current_store()
        for job in found:
            store.add(validate_job(job))
        flash(f'Discovery finished: {len(found)} matching opportunities found.', 'success')
        for error in errors:
            flash(error, 'error')
        return redirect('/sources')
