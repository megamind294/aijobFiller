"""Account-scoped inbox routes, authenticated OAuth and local-only reply editing."""
import hashlib
import secrets
import time

from flask import abort, flash, redirect, render_template, request, session, url_for

from .inbox import Inbox
from .mailbox_provider import GmailMailbox
from .web_auth import login_required
from .workspace import current_home, current_store


def register_inbox(app):
    def services():
        return Inbox(current_store()), GmailMailbox(current_home(), app.config)

    @app.get('/inbox')
    @login_required
    def inbox():
        box, provider = services()
        messages = box.rows()
        category_filter = request.args.get('category', '')
        if category_filter == 'REVIEW':
            messages = [m for m in messages if m['category'] == 'REVIEW' or m['routing'] == 'review']
        elif category_filter in {'INTERVIEW', 'REJECTED', 'OFFER'}:
            messages = [m for m in messages if m['category'] == category_filter]
        selected = request.args.get('message')
        try:
            active = box.get(selected) if selected else (messages[0] if messages else None)
        except ValueError:
            abort(404)
        return render_template('inbox.html', messages=messages, active=active,
                               thread=box.thread(active['id']) if active else [],
                               jobs=current_store().rows(), connected=provider.connected(),
                               configured=provider.configured(), category_filter=category_filter)

    @app.post('/inbox/sync')
    @login_required
    def inbox_sync():
        box, provider = services()
        try:
            count = box.ingest(provider.candidates())
            flash(f'Sync complete: {count} new candidate messages. Ambiguous messages need review.', 'success')
        except (ValueError, OSError, KeyError):
            flash('Could not sync Gmail. Check configuration or reconnect, then retry.', 'error')
        return redirect(url_for('inbox'))

    @app.route('/inbox/connect', methods=['GET', 'POST'])
    @login_required
    def inbox_connect():
        box, provider = services()
        if request.method == 'GET':
            return render_template('inbox_connect.html', configured=provider.configured())
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        try:
            url = provider.authorization_url(state, verifier)
        except (ValueError, ImportError):
            flash('Gmail is not configured. An administrator must configure Google OAuth and encrypted storage.', 'error')
            return redirect(url_for('inbox'))
        with current_store().connect() as db:
            db.execute('DELETE FROM inbox_oauth_states WHERE expires < ?', (time.time(),))
            old = session.get('inbox_oauth_state')
            if old:
                db.execute('DELETE FROM inbox_oauth_states WHERE digest=?', (hashlib.sha256(old.encode()).hexdigest(),))
            db.execute('INSERT INTO inbox_oauth_states VALUES(?,?)', (hashlib.sha256(state.encode()).hexdigest(), time.time() + 600))
        session['inbox_oauth_state'] = state
        session['inbox_oauth_verifier'] = verifier
        return redirect(url)

    @app.get('/inbox/callback')
    @login_required
    def inbox_callback():
        box, provider = services()
        expected = session.pop('inbox_oauth_state', '')
        verifier = session.pop('inbox_oauth_verifier', '')
        actual = request.args.get('state', '')
        if not expected or not actual or not secrets.compare_digest(expected, actual) or not verifier:
            abort(400)
        # Server consumption makes replay fail even if a client replays an older signed session cookie.
        with current_store().connect() as db:
            db.execute('BEGIN IMMEDIATE')
            consumed = db.execute('DELETE FROM inbox_oauth_states WHERE digest=? AND expires>=?',
                                  (hashlib.sha256(expected.encode()).hexdigest(), time.time())).rowcount
        if not consumed:
            abort(400)
        if request.args.get('error') or not request.args.get('code'):
            flash('Gmail connection was cancelled or not approved.', 'error')
            return redirect(url_for('inbox'))
        try:
            provider.exchange(request.args['code'], verifier)
            flash('Read-only Gmail connected. Choose Sync to fetch candidate messages.', 'success')
        except (ValueError, OSError, ImportError):
            flash('Gmail connection failed. Check configuration and try connecting again.', 'error')
        return redirect(url_for('inbox'))

    @app.post('/inbox/disconnect')
    @login_required
    def inbox_disconnect():
        box, provider = services()
        provider.disconnect()
        with current_store().connect() as db:
            db.execute('DELETE FROM inbox_oauth_states')
        session.pop('inbox_oauth_state', None)
        session.pop('inbox_oauth_verifier', None)
        flash('Gmail disconnected locally. Saved messages and drafts remain; revoke permission in your Google Account if needed.', 'success')
        return redirect(url_for('inbox'))

    @app.post('/inbox/<message_id>/route')
    @login_required
    def inbox_route(message_id):
        box, provider = services()
        try:
            box.route(message_id, request.form.get('job_id', ''))
        except ValueError:
            abort(404)
        flash('Routing corrected. Existing application outcomes were not replayed.', 'success')
        return redirect(url_for('inbox', message=message_id))

    @app.post('/inbox/<message_id>/draft')
    @login_required
    def inbox_draft(message_id):
        box, provider = services()
        try:
            box.get(message_id)
        except ValueError:
            abort(404)
        text = request.form.get('draft', '')
        if request.form.get('action') == 'suggest':
            # Acknowledgement only; no availability, qualifications or commitments are invented.
            text = 'Thank you for your message. I will review the details and follow up.'
        try:
            box.save_draft(message_id, text)
            flash('Local draft saved. Nothing was sent to Gmail.', 'success')
        except ValueError as error:
            flash(str(error), 'error')
        return redirect(url_for('inbox', message=message_id))
