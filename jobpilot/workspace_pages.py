"""Tracker, private networking notes, research and integration settings."""
import os
import secrets
import re
from collections import Counter

from flask import flash, redirect, render_template, request

from .core import canonical_url, read_json, write_json
from .inbox import Inbox
from .service import ProcessLock
from .web_accounts import provider_configuration
from .workspace import current_home, current_store


def register_workspace_pages(app):
    @app.get('/tracker')
    def tracker():
        store = current_store()
        jobs = store.rows()
        counts = Counter(job['status'] for job in jobs)
        messages = Inbox(store).rows()
        linked = {message['job_id'] for message in messages if message.get('job_id')}
        groups = [
            ('Preparing', [job for job in jobs if job['status'] in ('PREPARED', 'FILLING', 'NEEDS_REVIEW')]),
            ('Applied', [job for job in jobs if job['status'] in ('SUBMITTED', 'CONFIRMED')]),
            ('Linked replies', [job for job in jobs if job['id'] in linked]),
            ('Interview', [job for job in jobs if job['status'] == 'INTERVIEW']),
            ('Offer', [job for job in jobs if job['status'] == 'OFFER']),
            ('Rejected', [job for job in jobs if job['status'] == 'REJECTED']),
            ('Needs attention', [job for job in jobs if job['status'] in ('BLOCKED','SUBMISSION_UNCONFIRMED')]),
        ]
        return render_template('tracker.html', groups=groups, counts=counts, total=len(jobs), linked=len(linked))

    @app.route('/networking', methods=['GET', 'POST'])
    def networking():
        path = current_home() / 'networking.json'
        def saved():
            return read_json(path) if path.exists() else []
        if request.method == 'POST':
            try:
                contact = {field: request.form.get(field, '').strip() for field in ('name', 'company', 'email', 'url', 'notes', 'draft')}
                if not contact['name'] or any(len(value) > 5000 for value in contact.values()):
                    raise ValueError('Enter a contact name and keep each field below 5,000 characters.')
                if contact['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact['email']):
                    raise ValueError('Enter a valid contact email address.')
                if contact['url']:
                    contact['url'] = canonical_url(contact['url'])
                with ProcessLock(current_home() / 'web-action.lock'):
                    contacts = saved()
                    if len(contacts) >= 200:
                        raise ValueError('This workspace supports up to 200 private contacts.')
                    contacts.append({**contact, 'id': secrets.token_hex(16)})
                    write_json(path, contacts)
                flash('Contact and notes saved. No message has been sent.', 'success')
                return redirect('/networking')
            except (ValueError, OSError, RuntimeError) as error:
                flash(str(error), 'error')
        rows = saved()
        return render_template('networking.html', contacts=[row for row in rows if not row.get('archived')],
            archived=[row for row in rows if row.get('archived')],
            companies=sorted({job['company'] for job in current_store().rows()}))

    @app.post('/networking/<identifier>/update')
    def networking_update(identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            from flask import abort
            abort(404)
        try:
            with ProcessLock(current_home() / 'web-action.lock'):
                path = current_home() / 'networking.json'
                rows = read_json(path) if path.exists() else []
                row = next((item for item in rows if item['id'] == identifier), None)
                if row is None:
                    from flask import abort
                    abort(404)
                action = request.form.get('action', 'save')
                if action in ('archive', 'restore'):
                    row['archived'] = action == 'archive'
                elif action == 'save':
                    values = {key:request.form.get(key,'').strip() for key in ('name','company','email','url','notes','draft')}
                    if not values['name'] or any(len(value)>5000 for value in values.values()):
                        raise ValueError('Enter a contact name and keep each field under 5,000 characters.')
                    if values['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
                        raise ValueError('Enter a valid contact email address.')
                    if values['url']:
                        values['url'] = canonical_url(values['url'])
                    row.update(values)
                else:
                    raise ValueError('Unknown contact action.')
                write_json(path, rows)
            flash('Contact updated privately. No message was sent.', 'success')
        except (ValueError, OSError, RuntimeError) as exc:
            flash(str(exc), 'error')
        return redirect('/networking')

    @app.get('/research')
    def research():
        articles = [
            {'title': 'Keep your resume easy to read', 'category': 'RESUME GUIDE', 'text': 'Use clear headings, selectable text and facts you can verify. Review the exact document attached to each application.', 'url': 'https://support.greenhouse.io/hc/en-us/articles/200989175-Unsuccessful-resume-parse'},
            {'title': 'Work from the original posting', 'category': 'JOB SEARCH', 'text': 'Check the employer posting for location, required experience and current availability. A remote label alone does not establish eligibility.', 'url': 'https://eures.europa.eu/index_en'},
            {'title': 'Build a reviewable application', 'category': 'JOBPILOT WORKFLOW', 'text': 'Verify your profile, prepare a package, review the documents and answers, then check the employer confirmation. Changes to your source facts require a fresh package.', 'url': '/applications'},
        ]
        return render_template('research.html', articles=articles)

    @app.get('/settings')
    def settings():
        providers = provider_configuration()
        integrations = [
            ('Google sign-in', providers['google'], 'Sign in with a verified Google identity.', '/signin'),
            ('Email sign-in', providers['email'], 'One-time codes through your configured SMTP provider.', '/signin'),
            ('Phone sign-in', providers['phone'], 'One-time codes through your configured SMS provider.', '/signin'),
            ('Gmail routing', bool(os.environ.get('GOOGLE_CLIENT_ID') and os.environ.get('GOOGLE_CLIENT_SECRET') and os.environ.get('GOOGLE_GMAIL_REDIRECT_URI') and os.environ.get('MAILBOX_ENCRYPTION_KEY')), 'Connect your mailbox explicitly. Sync runs on demand.', '/inbox'),
            ('Customer billing', bool(os.environ.get('STRIPE_SECRET_KEY') and os.environ.get('STRIPE_WEBHOOK_SECRET')), 'Hosted checkout, available credits, refunds and cancellation.', '/billing'),
            ('ChatGPT connection', bool(app.config.get('MCP_PUBLIC_URL') or os.environ.get('MCP_PUBLIC_URL')), 'OAuth connection and revocable scoped API tokens.', '/connections/chatgpt'),
            ('Gemini assistant', bool(os.environ.get('GEMINI_API_KEY')), 'Needs a key configured securely on this server. Never paste secrets into chat.', '#assistant-setup'),
        ]
        return render_template('settings.html', integrations=integrations)

    @app.get('/automation')
    def automation_alias():
        return redirect('/auto-apply')

    @app.get('/dashboard')
    def dashboard_alias():
        return redirect('/')
