"""Configured customer sign-in; the existing private owner login stays separate."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import abort, current_app, flash, redirect, render_template, request, session


def provider_configuration():
    return {
        'google': all(os.environ.get(k) for k in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_SIGNIN_REDIRECT_URI')),
        'email': all(os.environ.get(k) for k in ('SMTP_HOST', 'SMTP_FROM')),
        'phone': all(os.environ.get(k) for k in ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_FROM')),
    }


def deliver_code(provider, subject, code):
    if provider == 'email':
        message = EmailMessage()
        message['From'] = os.environ['SMTP_FROM']
        message['To'] = subject
        message['Subject'] = 'Your JobPilot sign-in code'
        message.set_content('Your JobPilot sign-in code is ' + code + '. It expires in 10 minutes. If you did not request it, ignore this message.')
        port = int(os.environ.get('SMTP_PORT', '587'))
        with smtplib.SMTP(os.environ['SMTP_HOST'], port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            if os.environ.get('SMTP_USERNAME'):
                smtp.login(os.environ['SMTP_USERNAME'], os.environ.get('SMTP_PASSWORD', ''))
            smtp.send_message(message)
    else:
        sid = os.environ['TWILIO_ACCOUNT_SID']
        if not re.fullmatch(r'AC[a-fA-F0-9]{32}', sid):
            raise ValueError('SMS configuration is invalid.')
        data = urlencode({'To': subject, 'From': os.environ['TWILIO_FROM'], 'Body': 'Your JobPilot sign-in code is ' + code + '. Expires in 10 minutes.'}).encode()
        authorization = base64.b64encode((sid + ':' + os.environ['TWILIO_AUTH_TOKEN']).encode()).decode()
        call = Request('https://api.twilio.com/2010-04-01/Accounts/' + sid + '/Messages.json', data=data,
                       headers={'Authorization': 'Basic ' + authorization})
        with urlopen(call, timeout=15) as response:
            response.read(65536)


def register_accounts(app):
    accounts = app.extensions['jobpilot_accounts']

    def sign_in(account):
        from .web_auth import after_login_target
        target = after_login_target()
        session.clear()
        session.update(account_id=account['id'], auth_version=account['version'])
        session.permanent = True
        return redirect(target)

    @app.route('/register', methods=['GET', 'POST'])
    def account_register():
        error = None
        if request.method == 'POST':
            try:
                accounts.authentication_attempt(request.remote_addr or 'unknown', 'register', 5, 3600)
                password = request.form.get('password', '')
                if password != request.form.get('confirm_password', ''):
                    raise ValueError('Passwords do not match.')
                return sign_in(accounts.register_password(request.form.get('email', ''), password))
            except ValueError as exc:
                error = str(exc)
        return render_template('register.html', error=error), 400 if error else 200

    @app.route('/signin', methods=['GET', 'POST'])
    def account_signin():
        error = None
        status = 200
        if request.method == 'POST':
            try:
                accounts.authentication_attempt(request.remote_addr or 'unknown', 'password', 10, 300)
                account = accounts.password_account(request.form.get('email', ''), request.form.get('password', ''))
                if account:
                    return sign_in(account)
                error, status = 'Invalid email or password.', 401
            except ValueError as exc:
                error, status = str(exc), 429
        return render_template('signin.html', error=error, providers=provider_configuration(),
                               awaiting=bool(session.get('login_challenge'))), status

    @app.post('/signin/code')
    def account_code():
        provider = request.form.get('provider', 'email')
        subject = request.form.get('identity', '').strip()
        if provider == 'email':
            subject = subject.casefold()
        valid = (provider == 'email' and len(subject) <= 254 and re.fullmatch(r'[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+', subject)) or (provider == 'phone' and re.fullmatch(r'\+[1-9]\d{7,14}', subject))
        if not valid or not provider_configuration().get(provider):
            flash('Enter a valid address or phone number and use a configured sign-in provider.', 'error')
            return redirect('/signin')
        try:
            code = f'{secrets.randbelow(1000000):06d}'
            challenge = accounts.issue_challenge(provider, subject, code, current_app.secret_key)
            deliver_code(provider, subject, code)
            session['login_challenge'] = challenge
            flash('A sign-in code was sent. It expires in 10 minutes.', 'success')
        except Exception:
            flash('Verification delivery is unavailable. Try later or use another configured provider.', 'error')
        return redirect('/signin')

    @app.post('/signin/verify')
    def account_verify():
        try:
            provider, subject = accounts.consume_challenge(session.get('login_challenge', ''), request.form.get('code', ''), current_app.secret_key)
            account = accounts.identity(provider, subject, subject if provider == 'email' else '', verified=True)
            return sign_in(account)
        except ValueError as error:
            flash(str(error), 'error')
            return redirect('/signin')

    @app.get('/signin/google')
    def account_google():
        if not provider_configuration()['google']:
            flash('Google sign-in is not configured yet. Your existing owner login remains available.', 'error')
            return redirect('/signin')
        redirect_uri = os.environ['GOOGLE_SIGNIN_REDIRECT_URI']
        if not redirect_uri.startswith('https://'):
            abort(503)
        verifier = secrets.token_urlsafe(48)
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        session['google_signin'] = {'state': state, 'nonce': nonce, 'verifier': verifier, 'created': time.time()}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': redirect_uri, 'response_type': 'code',
            'scope': 'openid email profile', 'state': state, 'nonce': nonce,
            'code_challenge': challenge, 'code_challenge_method': 'S256'}))

    @app.get('/signin/google/callback')
    def account_google_callback():
        pending = session.pop('google_signin', None)
        if not pending or not hmac.compare_digest(pending['state'], request.args.get('state', '')) or time.time() - pending['created'] > 600:
            abort(400)
        if request.args.get('error') or not request.args.get('code') or not provider_configuration()['google']:
            flash('Google sign-in was canceled or is unavailable.', 'error')
            return redirect('/signin')
        try:
            call = Request('https://oauth2.googleapis.com/token', data=urlencode({
                'code': request.args['code'], 'client_id': os.environ['GOOGLE_CLIENT_ID'],
                'client_secret': os.environ['GOOGLE_CLIENT_SECRET'], 'redirect_uri': os.environ['GOOGLE_SIGNIN_REDIRECT_URI'],
                'grant_type': 'authorization_code', 'code_verifier': pending['verifier']}).encode())
            with urlopen(call, timeout=15) as response:
                data = response.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ValueError('Invalid identity response.')
            tokens = json.loads(data)
            from google.oauth2.id_token import verify_oauth2_token
            from google.auth.transport.requests import Request as GoogleRequest
            claims = verify_oauth2_token(tokens['id_token'], GoogleRequest(), os.environ['GOOGLE_CLIENT_ID'])
            if not claims.get('email_verified') or not hmac.compare_digest(claims.get('nonce', ''), pending['nonce']):
                raise ValueError('Google identity verification failed.')
            return sign_in(accounts.identity('google', claims['sub'], claims['email'], verified=True))
        except Exception:
            flash('Google sign-in could not be verified. Try again or use your existing provider.', 'error')
            return redirect('/signin')
