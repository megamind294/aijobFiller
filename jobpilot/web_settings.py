"""Profile configuration and explicitly requested public-feed discovery."""
import json
import re
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request

from .core import read_json, validate_profile, write_json
from .sources import DiscoveryBudget, discover
from .web_jobs import action_guard, validate_job


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
    home = Path(app.config['JOBPILOT_HOME'])
    profile_path = home / 'profile.json'
    sources_path = home / 'sources.json'

    def current_profile():
        return read_json(profile_path) if profile_path.exists() else read_json(Path(__file__).parent / 'templates/profile.example.json')

    @app.route('/profile', methods=['GET', 'POST'])
    def profile():
        candidate = current_profile()
        if request.method == 'POST':
            try:
                if 'profile_json' in request.form:
                    candidate = json.loads(request.form['profile_json'])
                else:
                    candidate = current_profile()
                    candidate.setdefault('personal', {})
                    for field in ('first_name', 'last_name', 'email', 'phone', 'city', 'country', 'state', 'address', 'postal_code', 'linkedin', 'github', 'website'):
                        candidate['personal'][field] = request.form.get('personal_' + field, '').strip()
                    candidate['summary'] = request.form.get('summary', '').strip()
                    for field in ('skills', 'languages'):
                        candidate[field] = [v.strip() for v in request.form.get(field, '').splitlines() if v.strip()]
                    for field, default in (('experience', []), ('projects', []), ('education', []), ('summary_variants', []), ('answers', {}), ('custom', {})):
                        try:
                            candidate[field] = json.loads(request.form.get(field + '_json', json.dumps(default)))
                        except json.JSONDecodeError as exc:
                            raise ValueError(f'{field}: {exc.msg} at line {exc.lineno}. Your edits are preserved below.') from None
                validate_profile_shape(candidate)
                candidate['facts_verified'] = request.form.get('facts_verified') == 'yes'
                if candidate['facts_verified']:
                    validate_profile(candidate)
                from .service import ProcessLock
                with ProcessLock(home / 'web-action.lock'):
                    write_json(profile_path, candidate)
                flash('Profile saved. ' + ('Your verified facts are ready for resumes.' if candidate['facts_verified'] else 'Draft saved; verify your facts before preparing resumes.'), 'success')
                return redirect('/profile')
            except (ValueError, TypeError, RuntimeError) as exc:
                flash('Another action is already running. Try again shortly.' if isinstance(exc, RuntimeError) else str(exc), 'error')
                candidate = current_profile()
        return render_template('profile.html', profile=candidate,
                               submitted=request.form if request.method == 'POST' else None)

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
        store = current_app.extensions['jobpilot_store']
        for job in found:
            store.add(validate_job(job))
        flash(f'Discovery finished: {len(found)} matching opportunities found.', 'success')
        for error in errors:
            flash(error, 'error')
        return redirect('/sources')
