"""Single-user authentication with persistent throttling and session revocation."""
import secrets
import sqlite3
import time
from functools import wraps
from contextlib import closing
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .core import read_json, write_json
from .service import ProcessLock


def auth_record():
    return read_json(current_app.config['AUTH_FILE'])


def authenticated():
    return bool(session.get('auth_version') and
                session['auth_version'] == auth_record()['version'])


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not authenticated():
            session.clear()
            return redirect(url_for('login'))
        return view(*args, **kwargs)
    return wrapped


def init_auth(app):
    auth_path = Path(app.config['JOBPILOT_HOME']) / 'web-auth.json'
    app.config['AUTH_FILE'] = str(auth_path)
    if not auth_path.exists():
        if not app.testing or not app.config.get('PASSWORD_HASH'):
            raise RuntimeError('Initialize private web-auth.json before starting JobPilot Web.')
        write_json(auth_path, {'username': 'admin', 'password_hash': app.config['PASSWORD_HASH'],
                               'version': app.config.get('AUTH_VERSION', secrets.token_hex(24))})
    record = read_json(auth_path)
    if not all(isinstance(record.get(k), str) and record[k] for k in ('username', 'password_hash', 'version')):
        raise RuntimeError('Invalid web authentication configuration.')
    db_path = Path(app.config['JOBPILOT_HOME']) / 'web-login.sqlite3'
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS failures (ip TEXT NOT NULL, at REAL NOT NULL)')

    @app.before_request
    def require_session():
        if request.endpoint in ('login', 'static'):
            return None
        if not authenticated():
            session.clear()
            return redirect(url_for('login'))

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            ip = request.remote_addr or 'unknown'
            stamp = time.time()
            with closing(sqlite3.connect(db_path, timeout=10)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM failures WHERE at < ?', (stamp - 300,))
                count = db.execute('SELECT COUNT(*) FROM failures WHERE ip = ?', (ip,)).fetchone()[0]
                total = db.execute('SELECT COUNT(*) FROM failures').fetchone()[0]
                if count >= 5 or total >= 30:
                    return render_template('login.html', error='Too many attempts. Try again in five minutes.'), 429
                credentials = auth_record()
                password_ok = check_password_hash(credentials['password_hash'], request.form.get('password', ''))
                if request.form.get('username') == credentials['username'] and password_ok:
                    db.execute('DELETE FROM failures WHERE ip = ?', (ip,))
                    session.clear()
                    session['auth_version'] = credentials['version']
                    session.permanent = True
                    return redirect(url_for('dashboard'))
                db.execute('INSERT INTO failures VALUES (?, ?)', (ip, stamp))
            return render_template('login.html', error='Invalid username or password.'), 401
        return render_template('login.html')

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.route('/password', methods=['GET', 'POST'])
    def password():
        if request.method == 'POST':
            with ProcessLock(Path(app.config['JOBPILOT_HOME']) / 'web-auth.lock'):
                credentials = auth_record()
                new = request.form.get('new_password', '')
                if not check_password_hash(credentials['password_hash'], request.form.get('current_password', '')):
                    flash('Current password is incorrect.', 'error')
                elif len(new) < 14 or len(new) > 256 or new != request.form.get('confirm_password'):
                    flash('Use matching passwords between 14 and 256 characters.', 'error')
                else:
                    credentials.update(password_hash=generate_password_hash(new), version=secrets.token_hex(24))
                    write_json(auth_path, credentials)
                    session.clear()
                    session['auth_version'] = credentials['version']
                    session.permanent = True
                    flash('Password updated. Other sessions have been signed out.', 'success')
                    return redirect(url_for('dashboard'))
        return render_template('password.html')
